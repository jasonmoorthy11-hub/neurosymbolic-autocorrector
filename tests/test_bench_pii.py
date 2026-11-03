"""Tests for scripts/bench_pii.py (external span-level PII benchmark). Offline."""

from __future__ import annotations

import http.client
import io
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import bench_pii  # noqa: E402

from autocorrector.guardrails.validators import PII_TYPES  # noqa: E402

SSN_TEXT = "Your SSN is 123-45-6789."  # ssn at 12..23


def _row(text: str, spans: list[tuple[str, int, int]], language: str = "English") -> dict:
    return {
        "text": text,
        "language": language,
        "pii_spans": json.dumps([{"label": lb, "start": s, "end": e} for lb, s, e in spans]),
    }


# --- Label map and span parsing ---


def test_bench_label_map_targets_our_types():
    assert set(bench_pii.LABEL_MAP.values()) <= set(PII_TYPES)
    assert bench_pii.LABEL_MAP["ssn"] == "ssn"
    assert bench_pii.LABEL_MAP["street_address"] == "address"
    assert "name" not in bench_pii.LABEL_MAP


def test_bench_parse_spans_valid():
    assert bench_pii.parse_spans('[{"label": "ssn", "start": 1, "end": 4}]', 4) == [("ssn", 1, 4)]


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"label": "ssn"}',
        '[{"label": "ssn", "start": 5, "end": 2}]',
        '[{"label": "ssn", "start": "1", "end": 4}]',
        '[{"start": 1, "end": 4}]',
        '[{"label": "ssn", "start": 3, "end": 3}]',  # empty
        '[{"label": "ssn", "start": 5, "end": 11}]',  # past the end of a 10-char text
        '[{"label": "ssn", "start": -1, "end": 4}]',
    ],
)
def test_bench_parse_spans_rejects_malformed(raw):
    with pytest.raises(ValueError):
        bench_pii.parse_spans(raw, 10)


# --- Scoring ---


def test_bench_same_type_overlap_is_true_positive():
    t = bench_pii.score_rows([_row(SSN_TEXT, [("ssn", 10, 23)])])
    assert (t.typed.pred_tp, t.typed.pred_fp, t.typed.gold_found, t.typed.gold_missed) == (
        1,
        0,
        1,
        0,
    )


def test_bench_wrong_type_is_fp_typed_but_tp_untyped():
    t = bench_pii.score_rows([_row(SSN_TEXT, [("phone_number", 12, 23)])])
    assert (t.typed.pred_tp, t.typed.pred_fp, t.typed.gold_missed) == (0, 1, 1)
    assert (t.untyped.pred_tp, t.untyped.pred_fp, t.untyped.gold_found) == (1, 0, 1)


def test_bench_overlap_with_out_of_scope_label_is_excluded():
    t = bench_pii.score_rows([_row(SSN_TEXT, [("customer_id", 12, 23)])])
    assert (t.typed.pred_tp, t.typed.pred_fp, t.typed.pred_excluded) == (0, 0, 1)
    assert t.untyped.pred_excluded == 1
    assert t.excluded_by_label == {"customer_id": 1}
    assert t.typed.gold_found + t.typed.gold_missed == 0


def test_bench_wrong_type_beats_out_of_scope_overlap():
    # In-scope gold of another type makes it a typed FP, even if out-of-scope gold overlaps too.
    t = bench_pii.score_rows([_row(SSN_TEXT, [("phone_number", 12, 23), ("customer_id", 12, 23)])])
    assert (t.typed.pred_fp, t.typed.pred_excluded) == (1, 0)
    assert t.excluded_by_label == {}


def test_bench_touching_spans_do_not_overlap():
    t = bench_pii.score_rows([_row(SSN_TEXT, [("ssn", 0, 12)])])  # ends where the SSN starts
    assert (t.typed.pred_tp, t.typed.pred_fp, t.typed.gold_missed) == (0, 1, 1)


def test_bench_unmatched_prediction_and_missed_gold():
    text = "Ship to Main Street. SSN 123-45-6789."
    t = bench_pii.score_rows([_row(text, [("street_address", 8, 19)])])
    assert (t.typed.pred_tp, t.typed.pred_fp, t.typed.gold_found, t.typed.gold_missed) == (
        0,
        1,
        0,
        1,
    )


def test_bench_per_type_counts():
    t = bench_pii.score_rows([_row(SSN_TEXT, [("ssn", 12, 23), ("email", 0, 4)])])
    assert t.per_type["ssn"].pred_tp == 1
    assert t.per_type["email"].gold_missed == 1


def test_bench_language_filter():
    rows = [_row(SSN_TEXT, [("ssn", 12, 23)]), _row(SSN_TEXT, [], language="German")]
    assert bench_pii.score_rows(rows, language="English").rows == 1
    assert bench_pii.score_rows(rows).rows == 2


def test_bench_skips_rows_with_bad_offsets():
    rows = [_row(SSN_TEXT, [("ssn", 12, 90)]), _row(SSN_TEXT, [("ssn", 12, 23)])]
    t = bench_pii.score_rows(rows)
    assert (t.rows, t.skipped_rows) == (1, 1)
    assert t.typed.pred_tp == 1


@pytest.mark.parametrize(
    "raw", ["not json", "{}", "[5]", '[{"label": "ssn", "start": "1", "end": 4}]']
)
def test_bench_structurally_malformed_rows_raise(raw):
    # Only offset errors are skipped; anything else means the data is not what we expect.
    row = {"text": SSN_TEXT, "language": "English", "pii_spans": raw}
    with pytest.raises(ValueError) as excinfo:
        bench_pii.score_rows([row])
    assert not isinstance(excinfo.value, bench_pii.SpanOffsetError)


def test_bench_offset_errors_are_span_offset_errors():
    with pytest.raises(bench_pii.SpanOffsetError):
        bench_pii.parse_spans('[{"label": "ssn", "start": 5, "end": 11}]', 10)


def test_bench_counts_mirror_mismatches(monkeypatch):
    assert bench_pii.score_rows([_row(SSN_TEXT, [])]).mirror_mismatches == 0
    monkeypatch.setattr(bench_pii, "find_pii_spans", lambda text: [])
    assert bench_pii.score_rows([_row(SSN_TEXT, [])]).mirror_mismatches == 1


# --- Fetching (urlopen mocked; no network) ---


def _api_row(text: str = SSN_TEXT, **overrides) -> dict:
    row = {"generated_text": text, "language": "English", "pii_spans": "[]"}
    return {"row": {**row, **overrides}, "truncated_cells": []}


def _fake_urlopen(pages: dict[int, dict], calls: list[int] | None = None):
    def urlopen(url, timeout):
        offset = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["offset"][0])
        if calls is not None:
            calls.append(offset)
        return io.BytesIO(json.dumps(pages[offset]).encode())

    return urlopen


@pytest.fixture
def no_sleep(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(bench_pii.time, "sleep", waits.append)
    return waits


def test_bench_fetch_pages_until_total(monkeypatch, tmp_path, no_sleep):
    pages = {
        0: {"num_rows_total": 3, "rows": [_api_row("a"), _api_row("b")]},
        2: {"num_rows_total": 3, "rows": [_api_row("c")]},
    }
    monkeypatch.setattr(bench_pii.urllib.request, "urlopen", _fake_urlopen(pages))
    cache = tmp_path / "test.jsonl"
    assert bench_pii.fetch(cache) == 3
    assert [json.loads(line)["text"] for line in cache.read_text().splitlines()] == ["a", "b", "c"]
    assert not cache.with_suffix(".partial").exists()


def test_bench_fetch_advances_by_rows_received(monkeypatch, tmp_path, no_sleep):
    calls: list[int] = []
    pages = {
        0: {"num_rows_total": 3, "rows": [_api_row("a")]},  # short page
        1: {"num_rows_total": 3, "rows": [_api_row("b"), _api_row("c")]},
    }
    monkeypatch.setattr(bench_pii.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    assert bench_pii.fetch(tmp_path / "test.jsonl") == 3
    assert calls == [0, 1]


@pytest.mark.parametrize(
    "bad_page",
    [
        {"num_rows_total": 3, "rows": []},  # empty before the end
        {"num_rows_total": 4, "rows": [_api_row("b")]},  # total changed
        {"num_rows_total": 3, "rows": [{"row": _api_row()["row"], "truncated_cells": ["x"]}]},
        {"num_rows_total": 3, "rows": [_api_row(pii_spans=5)]},  # wrong type
    ],
)
def test_bench_fetch_rejects_bad_pages_without_writing_cache(
    monkeypatch, tmp_path, no_sleep, bad_page
):
    pages = {0: {"num_rows_total": 3, "rows": [_api_row("a")]}, 1: bad_page}
    monkeypatch.setattr(bench_pii.urllib.request, "urlopen", _fake_urlopen(pages))
    cache = tmp_path / "test.jsonl"
    with pytest.raises(ValueError):
        bench_pii.fetch(cache)
    assert not cache.exists()
    assert not cache.with_suffix(".partial").exists()


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = http.client.HTTPMessage()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x", code, "err", headers, None)


def test_bench_get_retries_transient_errors(monkeypatch, no_sleep):
    outcomes = [
        _http_error(429, "7"),
        http.client.IncompleteRead(b""),
        _http_error(503),
        {"ok": 1},
    ]

    def urlopen(url, timeout):
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return io.BytesIO(json.dumps(item).encode())

    monkeypatch.setattr(bench_pii.urllib.request, "urlopen", urlopen)
    assert bench_pii._get("https://x") == {"ok": 1}
    assert no_sleep == [7, 4, 8]


def test_bench_get_does_not_retry_client_errors(monkeypatch, no_sleep):
    def urlopen(url, timeout):
        raise _http_error(404)

    monkeypatch.setattr(bench_pii.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        bench_pii._get("https://x")
    assert no_sleep == []


def test_bench_cache_is_anchored_at_repo_root():
    assert (
        Path(__file__).resolve().parent.parent / ".cache/gretel_pii/test.jsonl" == bench_pii.CACHE
    )
