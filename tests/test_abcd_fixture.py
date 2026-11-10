"""tests/fixtures/abcd_flagged.json: the adjudicated flags of the one real-turn audit run.

The fixture holds ids and labels only. These tests tie it to the numbers
docs/evaluation.md publishes and, when the ABCD archive is cached, to the data.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import bench_real_turns  # noqa: E402

FIXTURE = json.loads((REPO / "tests/fixtures/abcd_flagged.json").read_text(encoding="utf-8"))
RECORDS = FIXTURE["records"]
DOC = (REPO / "docs/evaluation.md").read_text(encoding="utf-8")
# flagged, true, false, ambiguous per context, as published
PUBLISHED = {
    "missing": (57, 16, 37, 4),
    "established": (50, 15, 31, 4),
    "late_purchase": (581, 243, 269, 69),
}


def test_abcd_fixture_rows_hold_ids_and_labels_but_no_text():
    assert len(RECORDS) == 688
    for row in RECORDS:
        assert set(row) == {"id", "context", "action", "reason", "label"}
        assert re.fullmatch(r"(dev|test)-\d+-\d+", row["id"])
        assert row["label"] in {"true", "false", "ambiguous"}
        assert row["action"] in {"correct", "escalate"}
    keys = [(row["id"], row["context"]) for row in RECORDS]
    assert len(set(keys)) == len(keys)


def test_abcd_fixture_counts_are_the_published_ones():
    assert set(row["context"] for row in RECORDS) == set(bench_real_turns.CONTEXTS)
    for context, (flagged, true, false, ambiguous) in PUBLISHED.items():
        labels = Counter(row["label"] for row in RECORDS if row["context"] == context)
        assert (sum(labels.values()), labels["true"], labels["false"], labels["ambiguous"]) == (
            flagged,
            true,
            false,
            ambiguous,
        )
        assert (
            f"| `{context}` | {flagged} | {true} | {false} | {ambiguous} | {true}/{flagged} = "
            in DOC
        )
        assert f"({flagged}/{FIXTURE['turns']}," in DOC


def test_abcd_fixture_per_reason_counts_are_the_published_ones():
    def cell(context: str, reason: str) -> str:
        rows = [r for r in RECORDS if r["context"] == context and r["reason"] == reason]
        labels = Counter(row["label"] for row in rows)
        return f"{len(rows)}: {labels['true']} / {labels['false']} / {labels['ambiguous']}"

    assert f"| REFUND-001 | – | – | {cell('late_purchase', 'REFUND-001')} |" in DOC
    assert cell("late_purchase", "REFUND-001") == "531: 228 / 238 / 65"
    for reason in (
        "pii:address",
        "pii:email",
        "pii:phone",
        "PROMISE-001",
        "REFUND-002",
        "DISC-001",
    ):
        cells = " | ".join(cell(context, reason) for context in PUBLISHED)
        assert f"| {reason} | {cells} |" in DOC
    assert f"| CREDIT-001 | {cell('missing', 'CREDIT-001')} | – | – |" in DOC


def test_abcd_fixture_changelog_states_the_same_precision():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    flat = " ".join(changelog.split())
    for flagged, true, _, _ in PUBLISHED.values():
        assert f"{true} of {flagged} ({true / flagged:.1%})" in flat


def test_abcd_fixture_a_turn_has_one_label_per_reason_in_every_context():
    labels: dict[tuple[str, str], set[str]] = {}
    for row in RECORDS:
        labels.setdefault((row["id"], row["reason"]), set()).add(row["label"])
    assert all(len(seen) == 1 for seen in labels.values())


def test_abcd_fixture_language_flags_are_all_false():
    language = [row for row in RECORDS if row["reason"].startswith("language:")]
    assert len(language) == 84 and {row["label"] for row in language} == {"false"}
    assert len({row["reason"] for row in language}) == 16


def test_abcd_fixture_digest_is_the_one_the_run_printed():
    rows = sorted((r["id"], r["context"], r["action"], r["reason"]) for r in RECORDS)
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == FIXTURE["flagged_sha256"]
    assert f"flagged sha256: {digest}" in DOC
    assert FIXTURE["run_commit"][:7] in DOC and f"commit {FIXTURE['run_commit']}" in DOC


def test_abcd_fixture_ids_point_at_agent_turns_of_the_pinned_archive():
    cache = bench_real_turns.CACHE
    if not cache.exists():
        pytest.skip("ABCD archive not cached (.cache/abcd/); it is never committed")
    bench_real_turns.verify_archive(cache)
    known = {turn["id"] for turn in bench_real_turns.abcd_turns(cache, ["dev", "test"])}
    assert len(known) == FIXTURE["turns"]
    assert {row["id"] for row in RECORDS} <= known
