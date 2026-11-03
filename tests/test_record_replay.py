"""Tests for scripts/record_replay.py (offline replay data for the hosted demo)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import record_replay  # noqa: E402
from scenarios import PIPELINE_SCENARIOS, STEP_SCENARIOS  # noqa: E402

from autocorrector.dashboard.api import DEMO_SCENARIOS  # noqa: E402
from autocorrector.knowledge_graph.store import KnowledgeGraphStore  # noqa: E402

SCENARIO_KEYS = {
    "id",
    "title",
    "agent_id",
    "input",
    "customer_context",
    "detected_language",
    "intent",
    "intent_confidence",
    "pii_types",
    "violations",
    "fact_errors",
    "edits",
    "action",
    "was_modified",
    "corrected_text",
    "severity",
    "heuristic_confidence",
    "guardrails_applied",
    "explanation",
    "latency_ms",
    "t_offset_ms",
}


@pytest.fixture
def no_keys(monkeypatch, tmp_path):
    for var in record_replay.KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("AUTOCORRECTOR_NEMO_RAILS_ENABLED", raising=False)
    # Settings also read ./.env: run from an empty directory
    monkeypatch.chdir(tmp_path)


def test_scenarios_module_lists_demo_inputs():
    assert len(STEP_SCENARIOS) == 10
    assert len(PIPELINE_SCENARIOS) == 5
    assert STEP_SCENARIOS[0].text == "I can offer you a $200 credit for the inconvenience."
    assert PIPELINE_SCENARIOS[-1].name == "Pipeline: Clean Output"


def test_replay_inputs_cover_demo_and_dashboard_scenarios():
    inputs = record_replay.replay_inputs()

    assert [i.id for i in inputs] == [f"S{n:02d}" for n in range(1, 26)]
    assert [i.text for i in inputs[15:]] == [d["text"] for d in DEMO_SCENARIOS]
    assert [i.agent_id for i in inputs[15:]] == [d["agent_id"] for d in DEMO_SCENARIOS]
    assert {i.agent_id for i in inputs} <= set(record_replay.AGENTS)
    assert all(i.title for i in inputs)


async def test_record_builds_schema_for_correct_and_escalate():
    inputs = record_replay.replay_inputs()
    picked = [inputs[0], inputs[3]]  # $200 credit (correct), $1,000 credit (escalate)
    kg = KnowledgeGraphStore()
    await kg.connect()
    try:
        records = await record_replay.record(picked, kg)
    finally:
        await kg.close()

    corrected, escalated = records
    assert set(corrected) == SCENARIO_KEYS
    assert corrected["action"] == "correct"
    assert corrected["t_offset_ms"] == 0
    assert escalated["t_offset_ms"] == record_replay.SPACING_MS
    assert [e["rule_id"] for e in corrected["edits"]] == ["CREDIT-001"]
    assert corrected["edits"][0]["tier"] == "span"
    assert corrected["corrected_text"] != corrected["input"]
    assert corrected["violations"][0]["source_document"]

    assert escalated["action"] == "escalate"
    assert escalated["corrected_text"] is None
    assert escalated["edits"] == []
    assert escalated["severity"] == "critical"


async def test_record_lists_pii_types_in_pattern_order():
    inputs = record_replay.replay_inputs()
    kg = KnowledgeGraphStore()
    await kg.connect()
    try:
        [rec] = await record_replay.record([inputs[4]], kg)  # SSN + email
    finally:
        await kg.close()

    assert rec["pii_types"] == ["ssn", "email"]
    assert "412-55-7890" not in (rec["corrected_text"] or "")


def test_build_file_header():
    data = record_replay.build_file(
        [], recorded_at="2026-11-03T00:00:00Z", git_sha="abc1234", rules=[]
    )

    assert data["schema_version"] == 1
    assert data["roi_assumptions"] == {"cost_per_violation": 15.0, "cost_per_escalation": 25.0}
    assert data["rules"] == []
    assert data["mode"] == "offline-deterministic"
    assert data["recorded_at"] == "2026-11-03T00:00:00Z"
    assert data["git_sha"] == "abc1234"
    assert data["python"]
    assert "warm-up" in data["latency_note"]
    assert data["scenarios"] == []


def test_main_writes_file_and_check_passes(tmp_path, no_keys):
    out = tmp_path / "replay.json"

    assert record_replay.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert len(data["scenarios"]) == 25
    rules = {r["rule_id"]: r for r in data["rules"]}
    assert len(rules) == 8
    assert rules["CREDIT-002"]["severity"] == "critical"
    assert rules["CREDIT-002"]["source_section"] == "Section 4.3"
    assert "violation_count" not in rules["CREDIT-002"]
    assert record_replay.main(["--out", str(out), "--check"]) == 0


def test_check_ignores_run_specific_fields(tmp_path, no_keys):
    out = tmp_path / "replay.json"
    record_replay.main(["--out", str(out)])
    data = json.loads(out.read_text())
    data["recorded_at"] = "1999-01-01T00:00:00Z"
    data["git_sha"] = "0000000"
    data["python"] = "0.0.0"
    data["scenarios"][0]["latency_ms"] = 999.9
    out.write_text(json.dumps(data))

    assert record_replay.main(["--out", str(out), "--check"]) == 0


def test_check_detects_drift(tmp_path, no_keys, capsys):
    out = tmp_path / "replay.json"
    record_replay.main(["--out", str(out)])
    data = json.loads(out.read_text())
    data["scenarios"][0]["action"] = "allow"
    out.write_text(json.dumps(data))

    assert record_replay.main(["--out", str(out), "--check"]) == 1
    assert '"action": "allow"' in capsys.readouterr().out


def test_check_fails_when_file_missing(tmp_path, no_keys):
    assert record_replay.main(["--out", str(tmp_path / "absent.json"), "--check"]) == 1


def test_main_refuses_when_api_key_set(tmp_path, no_keys, monkeypatch):
    out = tmp_path / "replay.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    assert record_replay.main(["--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize("var", ["AUTOCORRECTOR_ANTHROPIC_API_KEY", "AUTOCORRECTOR_OPENAI_API_KEY"])
def test_main_refuses_when_prefixed_key_set(tmp_path, no_keys, monkeypatch, var):
    monkeypatch.setenv(var, "sk-test")

    assert record_replay.main(["--out", str(tmp_path / "replay.json")]) == 2


def test_main_refuses_when_key_only_in_dotenv(tmp_path, no_keys):
    (tmp_path / ".env").write_text("AUTOCORRECTOR_ANTHROPIC_API_KEY=sk-test\n")

    assert record_replay.main(["--out", str(tmp_path / "replay.json")]) == 2


def test_main_refuses_when_nemo_rails_enabled(tmp_path, no_keys, monkeypatch):
    monkeypatch.setenv("AUTOCORRECTOR_NEMO_RAILS_ENABLED", "true")

    assert record_replay.main(["--out", str(tmp_path / "replay.json")]) == 2


def test_check_reports_unreadable_file(tmp_path, no_keys, capsys):
    out = tmp_path / "replay.json"
    out.write_text("not json")

    assert record_replay.main(["--out", str(out), "--check"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
