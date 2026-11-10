"""Tests for scripts/bench_real_turns.py (real-turn audit). Offline; no ABCD text is read."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import bench_real_turns  # noqa: E402

SSN_TURN = "Your SSN is 123-45-6789."


def _archive(tmp_path: Path) -> Path:
    data = {
        "train": [],
        "dev": [
            {
                "convo_id": 7,
                "original": [
                    ["agent", "Hello, how can I help?"],
                    ["customer", "My order is late."],
                    ["action", "Searching the FAQ pages ..."],
                    ["agent", "Let me check."],
                ],
            }
        ],
        "test": [{"convo_id": 9, "original": [["customer", "Hi"], ["agent", "Hi there."]]}],
    }
    path = tmp_path / "abcd.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def _record(**overrides) -> dict:
    base = {
        "id": "dev-1-0",
        "context": "missing",
        "text": "x",
        "action": "correct",
        "rule_ids": [],
        "fact_errors": [],
        "pii_spans": [],
        "guardrails_applied": [],
        "edits": [],
        "pii_types": [],
        "language": "en",
        "corrected_text": "y",
    }
    return {**base, **overrides}


def _span(ptype: str) -> dict:
    return {"type": ptype, "start": 0, "end": 1, "text": "x"}


# --- Reading turns ---


def test_real_turns_abcd_yields_only_agent_turns_with_stable_ids(tmp_path):
    path = _archive(tmp_path)
    assert list(bench_real_turns.abcd_turns(path, ["dev"])) == [
        {"id": "dev-7-0", "text": "Hello, how can I help?"},
        {"id": "dev-7-3", "text": "Let me check."},
    ]
    assert [t["id"] for t in bench_real_turns.abcd_turns(path, ["test"])] == ["test-9-1"]


def test_real_turns_abcd_reads_each_named_split_once(tmp_path):
    turns = list(bench_real_turns.abcd_turns(_archive(tmp_path), ["test", "dev", "test"]))
    assert [t["id"] for t in turns] == ["test-9-1", "dev-7-0", "dev-7-3"]


def test_real_turns_fetch_writes_the_downloaded_archive(monkeypatch, tmp_path):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"archive bytes"

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(bench_real_turns.urllib.request, "urlopen", fake_urlopen)
    cache = tmp_path / "nested" / "abcd.json.gz"
    bench_real_turns.fetch(cache)
    assert cache.read_bytes() == b"archive bytes"
    assert bench_real_turns.ABCD_COMMIT in seen["url"] and seen["url"].startswith("https://")
    assert list(cache.parent.iterdir()) == [cache]


def test_real_turns_archive_must_match_the_pinned_hash(tmp_path):
    path = _archive(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        bench_real_turns.verify_archive(path)
    assert "sha256" in str(excinfo.value)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bench_real_turns.verify_archive(path, expected=digest)  # no error


def test_real_turns_jsonl_rows_get_ids_and_keep_context(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(
        '{"text": "One."}\n\n{"id": 42, "text": "Two.", "context": {"account_tenure_days": 5}}\n',
        encoding="utf-8",
    )
    assert list(bench_real_turns.jsonl_turns(path)) == [
        {"id": "1", "text": "One."},
        {"id": "42", "text": "Two.", "context": {"account_tenure_days": 5}},
    ]


@pytest.mark.parametrize(
    "line",
    ['{"id": 1}', '{"text": 5}', '["text"]', '{"text": "ok", "context": "new"}', "not json"],
)
def test_real_turns_jsonl_rejects_a_bad_line_with_its_number(tmp_path, line):
    path = tmp_path / "turns.jsonl"
    path.write_text('{"text": "fine"}\n' + line + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        list(bench_real_turns.jsonl_turns(path))
    assert f"{path}:2:" in str(excinfo.value)


# --- Labels and digest ---


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"rule_ids": ["ESC-001"]}, "ESC-001"),
        (
            {"rule_ids": ["SLA-001", "PROMISE-001"], "pii_spans": [_span("phone")]},
            "PROMISE-001+SLA-001+pii:phone",
        ),
        ({"fact_errors": [{"claim": "$5", "correct_value": "$4"}]}, "FACT-CHECK"),
        ({"pii_spans": [_span("email"), _span("ssn"), _span("email")]}, "pii:email+pii:ssn"),
        # pii_types can name a type with no redacted span: only spans are judged
        (
            {"pii_types": ["bank_account", "phone"], "pii_spans": [_span("bank_account")]},
            "pii:bank_account",
        ),
        ({"language": "es", "action": "escalate"}, "language:es"),
        # A non-English detection is always named: it alone escalates
        (
            {"language": "es", "pii_spans": [_span("email")], "action": "escalate"},
            "pii:email+language:es",
        ),
        ({"rule_ids": ["SLA-001", "DISC-001"]}, "DISC-001+SLA-001"),
        (
            {"guardrails_applied": ["validation-unavailable"], "action": "escalate"},
            "validation-unavailable",
        ),
        ({"action": "error", "error": "ValueError: x"}, "error"),
        ({"action": "escalate"}, "other"),
    ],
)
def test_real_turns_reason_labels(overrides, expected):
    assert bench_real_turns.reason(_record(**overrides)) == expected


def test_real_turns_flagged_digest_ignores_order_and_text_but_not_labels():
    a = _record(id="dev-1-0", pii_spans=[_span("ssn")])
    b = _record(id="dev-2-4", rule_ids=["ESC-001"], action="escalate")
    digest = bench_real_turns.flagged_digest([a, b])
    assert digest == bench_real_turns.flagged_digest(
        [b, {**a, "text": "other", "corrected_text": ""}]
    )
    assert digest != bench_real_turns.flagged_digest([a, {**b, "action": "correct"}])
    assert digest != bench_real_turns.flagged_digest([a])


# --- Running the pipeline ---


async def test_real_turns_run_flags_non_allowed_turns_per_context():
    turns = [
        {"id": "a", "text": "Thanks for waiting."},
        {"id": "b", "text": SSN_TURN},
        {"id": "c", "text": SSN_TURN},
    ]
    flagged = await bench_real_turns.run_turns(
        turns, {"one": {}, "two": {"account_tenure_days": 365}}
    )
    assert [(r["id"], r["context"]) for r in flagged] == [
        ("b", "one"),
        ("c", "one"),
        ("b", "two"),
        ("c", "two"),
    ]
    first = flagged[0]
    assert (first["action"], first["pii_types"], first["text"]) == ("correct", ["ssn"], SSN_TURN)
    assert "123-45-6789" not in first["corrected_text"]
    assert first["pii_spans"] == [{"type": "ssn", "start": 12, "end": 23, "text": "123-45-6789"}]
    assert first["fact_errors"] == [] and isinstance(first["guardrails_applied"], list)
    assert bench_real_turns.reason(first) == "pii:ssn"


async def test_real_turns_record_keeps_pii_spans_a_template_would_erase():
    text = "Our internal tool shows your email is a@b.co."
    (record,) = await bench_real_turns.run_turns([{"id": "t", "text": text}], {"one": {}})
    assert "DISC-001" in record["rule_ids"]
    assert [span["text"] for span in record["pii_spans"]] == ["a@b.co"]
    assert record["edits"] and record["edits"][0]["rule_id"]


async def test_real_turns_refund_rule_fires_only_in_the_late_purchase_context():
    turns = [{"id": "r", "text": "I will process your refund right away."}]
    flagged = await bench_real_turns.run_turns(turns, bench_real_turns.CONTEXTS)
    assert list(bench_real_turns.CONTEXTS) == ["missing", "established", "late_purchase"]
    assert [(r["context"], r["rule_ids"]) for r in flagged] == [("late_purchase", ["REFUND-001"])]


async def test_real_turns_a_non_english_turn_is_labelled_by_language_even_with_pii():
    text = "Muchas gracias por esperar, su correo electrónico es a@b.co y ya está actualizado."
    (record,) = await bench_real_turns.run_turns([{"id": "es", "text": text}], {"one": {}})
    assert record["action"] == "escalate"
    assert bench_real_turns.reason(record).endswith("language:es")


async def test_real_turns_a_run_that_raises_is_recorded_and_the_audit_goes_on(monkeypatch):
    import autocorrector.orchestrator.pipeline as pipeline

    real = pipeline.run_autocorrection

    async def flaky(text, **kwargs):
        if "boom" in text:
            raise RuntimeError("pipeline broke")
        return await real(text, **kwargs)

    monkeypatch.setattr(pipeline, "run_autocorrection", flaky)
    turns = [{"id": "a", "text": "boom"}, {"id": "b", "text": SSN_TURN}]
    flagged = await bench_real_turns.run_turns(turns, {"one": {}})
    assert [(r["id"], r["action"]) for r in flagged] == [("a", "error"), ("b", "correct")]
    assert flagged[0]["error"] == "RuntimeError: pipeline broke"
    assert bench_real_turns.reason(flagged[0]) == "error"


async def test_real_turns_a_turn_context_overrides_the_base_context():
    offer = "I can offer you a $200 credit for the trouble."
    turns = [
        {"id": "new", "text": offer, "context": {"account_tenure_days": 5}},
        {"id": "old", "text": offer},
    ]
    flagged = await bench_real_turns.run_turns(turns, {"given": {"account_tenure_days": 400}})
    assert [r["id"] for r in flagged] == ["new"]
    assert flagged[0]["rule_ids"] == ["CREDIT-001"]


def test_real_turns_report_lists_a_context_with_no_flags(capsys):
    turns = [{"id": "a", "text": "Hi."}, {"id": "b", "text": "Hi."}, {"id": "c", "text": "Bye."}]
    flagged = [_record(id="a", context="missing", text="Hi.", pii_spans=[_span("ssn")])]
    bench_real_turns.print_report("unit", turns, flagged, ["missing", "established"])
    out = capsys.readouterr().out
    assert "Agent turns: 3 (2 distinct texts)" in out
    missing, established = out.split("## Context: ")[1:]
    assert "33.33% (1/3," in missing and "1  pii:ssn" in missing
    assert "0.00% (0/3," in established
    assert "flagged sha256: " + bench_real_turns.flagged_digest(flagged) in out


# --- Command line ---


def _run(*argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    # Keep the empty keys conftest pins: without them the child would read a developer's .env
    base = {
        **{k: v for k, v in os.environ.items() if not k.endswith("API_KEY")},
        "AUTOCORRECTOR_ANTHROPIC_API_KEY": "",
        "AUTOCORRECTOR_OPENAI_API_KEY": "",
    }
    return subprocess.run(
        [sys.executable, "scripts/bench_real_turns.py", *argv],
        cwd=REPO,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_real_turns_cli_runs_a_jsonl_file_and_writes_the_flagged_turns(tmp_path):
    source = tmp_path / "turns.jsonl"
    source.write_text(
        json.dumps({"id": "t1", "text": "Thanks for waiting."})
        + "\n"
        + json.dumps({"id": "t2", "text": SSN_TURN})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "flagged.jsonl"
    proc = _run("--input", str(source), "--flagged", str(out))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "Agent turns: 2 (2 distinct texts)" in proc.stdout
    assert "50.00% (1/2," in proc.stdout
    (record,) = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert (record["id"], record["context"], record["action"]) == ("t2", "given", "correct")


def test_real_turns_cli_stops_on_an_archive_that_is_not_the_pinned_one(tmp_path):
    proc = _run("--cache", str(_archive(tmp_path)))
    assert proc.returncode != 0
    assert "is not the pinned ABCD v1.1 archive" in proc.stderr
    assert "Agent turns" not in proc.stdout


async def test_real_turns_reason_names_only_redacted_span_types():
    text = "I have your account number: 1234567890 on file."
    (record,) = await bench_real_turns.run_turns([{"id": "t", "text": text}], {"one": {}})
    assert [span["type"] for span in record["pii_spans"]] == ["bank_account"]
    assert bench_real_turns.reason(record) == "pii:bank_account"


async def test_real_turns_preflight_passes_on_the_offline_pipeline():
    await bench_real_turns.preflight()


async def test_real_turns_preflight_stops_when_language_detection_is_off(monkeypatch):
    import autocorrector.multilingual.detector as detector

    monkeypatch.setattr(detector, "detect_language", lambda text: "en")
    with pytest.raises(SystemExit) as excinfo:
        await bench_real_turns.preflight()
    assert "Preflight failed, nothing was run on the data" in str(excinfo.value)
    assert "language:es" in str(excinfo.value)


def test_real_turns_abcd_mode_runs_every_context_and_writes_before_reporting(
    monkeypatch, tmp_path, capsys
):
    """The path the registered run takes, on a fake archive."""
    archive = _archive(tmp_path)
    with gzip.open(archive, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    data["dev"][0]["original"].append(["agent", SSN_TURN])
    with gzip.open(archive, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)
    monkeypatch.setattr(
        bench_real_turns, "ABCD_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(bench_real_turns, "EXPECTED_TURNS", {"dev": 3, "test": 1, "train": 0})
    out = tmp_path / "flagged.jsonl"
    assert bench_real_turns.main(["--cache", str(archive), "--flagged", str(out)]) == 0
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(r["id"], r["context"]) for r in records] == [
        ("dev-7-4", "missing"),
        ("dev-7-4", "established"),
        ("dev-7-4", "late_purchase"),
    ]
    report = capsys.readouterr().out
    assert "dev + test, agent turns" in report and "Agent turns: 4 (4 distinct texts)" in report
    assert report.count("## Context: ") == 3
    assert report.splitlines()[1].startswith("commit ")

    monkeypatch.setattr(bench_real_turns, "EXPECTED_TURNS", {"dev": 9600, "test": 9544})
    with pytest.raises(SystemExit) as excinfo:
        bench_real_turns.main(["--cache", str(archive)])
    assert "Expected 19144 agent turns" in str(excinfo.value)


def test_real_turns_cli_opens_the_output_before_running(monkeypatch, tmp_path):
    source = tmp_path / "turns.jsonl"
    source.write_text('{"text": "Hi."}\n', encoding="utf-8")

    async def must_not_run(turns, contexts):
        raise AssertionError("the pipeline ran before the output path was checked")

    monkeypatch.setattr(bench_real_turns, "run_turns", must_not_run)
    with pytest.raises(FileNotFoundError):
        bench_real_turns.main(
            ["--input", str(source), "--flagged", str(tmp_path / "missing" / "out.jsonl")]
        )


@pytest.mark.parametrize(
    "env",
    [
        {"AUTOCORRECTOR_ANTHROPIC_API_KEY": "not-a-real-key"},
        {"ANTHROPIC_API_KEY": "not-a-real-key"},
        {"AUTOCORRECTOR_OPENAI_API_KEY": "not-a-real-key"},
        {"OPENAI_API_KEY": "not-a-real-key"},
        {"AUTOCORRECTOR_NEMO_RAILS_ENABLED": "true"},
    ],
)
def test_real_turns_cli_refuses_when_a_model_could_run(tmp_path, env):
    source = tmp_path / "turns.jsonl"
    source.write_text('{"text": "Hi."}\n', encoding="utf-8")
    proc = _run("--input", str(source), **env)
    assert proc.returncode != 0
    assert "Refusing to run" in proc.stderr
    assert "Agent turns" not in proc.stdout
