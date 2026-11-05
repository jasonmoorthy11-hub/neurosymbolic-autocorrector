"""Tests for scripts/record_replay.py (offline replay data for the hosted demo)."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
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


@pytest.fixture(autouse=True)
def _restore_pipeline_store():
    # run_autocorrection(kg_store=...) installs the store as the pipeline singleton;
    # a recorder's stub-backed store must not leak into later pipeline tests
    from autocorrector.orchestrator import pipeline

    before = pipeline._kg_store
    yield
    pipeline._kg_store = before


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


# --- LLM-tier recording (--llm): the one recorded run behind a cost guard ---------------

FAKE_KEY = "sk-ant-api03-FAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKEKEY"


def _usage(input_tokens=1200, output_tokens=300, cache_creation=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def _response(text="<rewrite>", usage=None):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=usage or _usage(),
    )


class StubClient:
    """Stands in for anthropic.AsyncAnthropic: records calls, returns a canned response."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or _response()
        self.responses: list = []  # consumed first, one per call, when set
        self.error = error
        self.requests: list[dict] = []
        self.env_key_at_call: list[str | None] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        self.env_key_at_call.append(os.environ.get(record_replay.LLM_KEY_VAR))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0) if self.responses else self.response


@pytest.fixture
def llm_env(no_keys, monkeypatch):
    """Only the LLM key var is set; the SDK client is replaced by a stub."""
    monkeypatch.setenv(record_replay.LLM_KEY_VAR, FAKE_KEY)
    stub = StubClient()
    monkeypatch.setattr(record_replay, "_make_client", lambda key: stub)
    return stub


def test_llm_constants():
    assert record_replay.LLM_MODEL == "claude-haiku-5-5"
    assert record_replay.LLM_MODE == "llm-recorded"
    assert record_replay.LLM_REPLAY_PATH.name == "replay_llm.json"
    assert record_replay.LLM_REPLAY_PATH.parent == record_replay.REPLAY_PATH.parent
    assert record_replay.LLM_HARD_USD == 1.0
    assert record_replay.LLM_SOFT_USD < record_replay.LLM_HARD_USD


def test_llm_refuses_without_env_key(tmp_path, no_keys):
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 2
    assert not out.exists()


def test_llm_refuses_when_another_key_var_is_set(tmp_path, llm_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 2
    assert not out.exists()
    assert llm_env.requests == []


def test_llm_refuses_when_dotenv_defines_a_key(tmp_path, llm_env):
    (tmp_path / ".env").write_text("AUTOCORRECTOR_OPENAI_API_KEY=sk-test\n")
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 2
    assert not out.exists()


def test_llm_refuses_when_nemo_rails_enabled(tmp_path, llm_env, monkeypatch):
    monkeypatch.setenv("AUTOCORRECTOR_NEMO_RAILS_ENABLED", "true")

    assert record_replay.main(["--llm", "--out", str(tmp_path / "replay_llm.json")]) == 2


@pytest.mark.parametrize("var", ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"])
def test_llm_refuses_when_the_sdk_could_be_redirected(tmp_path, llm_env, monkeypatch, var):
    # The SDK reads both: the key would go to another host, or a second credential along
    monkeypatch.setenv(var, "https://proxy.example.test" if "URL" in var else "tok")

    assert record_replay.main(["--llm", "--out", str(tmp_path / "replay_llm.json")]) == 2
    assert llm_env.requests == []


def test_llm_refuses_when_dotenv_defines_a_redirect_var(tmp_path, llm_env):
    (tmp_path / ".env").write_text("ANTHROPIC_BASE_URL=https://proxy.example.test\n")

    assert record_replay.main(["--llm", "--out", str(tmp_path / "replay_llm.json")]) == 2


def test_llm_removes_the_key_from_the_environment_before_the_pipeline_runs(tmp_path, llm_env):
    # Settings-built clients (translation, a fallback store) would bypass the guard:
    # with the var gone, get_settings().anthropic_api_key is empty for the whole run
    assert record_replay.main(["--llm", "--out", str(tmp_path / "replay_llm.json")]) == 0

    assert llm_env.env_key_at_call and set(llm_env.env_key_at_call) == {None}


def test_llm_voids_the_run_when_a_scenario_is_not_english(tmp_path, llm_env, monkeypatch):
    # A non-English scenario would be translated by an unguarded client; the run is void
    real = record_replay.run_autocorrection
    flagged = record_replay.replay_inputs()[2].text

    async def fake_run(text, context, **kwargs):
        result = await real(text, context, **kwargs)
        return result.model_copy(update={"detected_language": "fr"}) if text == flagged else result

    monkeypatch.setattr(record_replay, "run_autocorrection", fake_run)
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3
    assert not out.exists()


def test_llm_and_check_are_mutually_exclusive(tmp_path, llm_env):
    with pytest.raises(SystemExit) as exc:
        record_replay.main(["--llm", "--check", "--out", str(tmp_path / "x.json")])

    assert exc.value.code == 2
    assert llm_env.requests == []


def test_llm_records_every_corrected_scenario_with_the_llm_tier(tmp_path, llm_env, capsys):
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 0

    text = out.read_text()
    assert "sk-ant" not in text
    data = json.loads(text)
    assert data["mode"] == "llm-recorded"
    assert data["latency_note"] == record_replay.LLM_LATENCY_NOTE
    assert len(data["scenarios"]) == 25
    corrected = [s for s in data["scenarios"] if s["action"] == "correct" and s["violations"]]
    assert corrected
    for s in corrected:
        assert [e["tier"] for e in s["edits"]] == ["llm"], s["id"]
        assert s["corrected_text"] == "<rewrite>"
    calls = len(llm_env.requests)
    assert calls > 0
    assert all(r["model"] == "claude-haiku-5-5" for r in llm_env.requests)
    assert data["llm"] == {
        "model": "claude-haiku-5-5",
        "calls": calls,
        "input_tokens": 1200 * calls,
        "output_tokens": 300 * calls,
        "usd": round(calls * (1200 * 0.10 + 300 * 0.50) / 1e6, 4),
        "recorded_with_sdk": anthropic.__version__,
    }
    assert "sk-ant" not in capsys.readouterr().out


def test_llm_aborts_without_a_file_when_the_sdk_raises(tmp_path, llm_env, capsys):
    llm_env.error = RuntimeError(f"authentication_error: invalid x-api-key {FAKE_KEY}")
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3

    assert not out.exists()
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "<redacted>" in err
    assert FAKE_KEY not in err
    assert len(llm_env.requests) == 1  # aborted after the first failing scenario


def test_llm_aborts_without_a_file_on_the_soft_budget_trip(tmp_path, llm_env, capsys):
    # One call costing $0.95 in output tokens: past the soft cap, under the hard cap
    llm_env.response = _response(usage=_usage(input_tokens=0, output_tokens=1_900_000))
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3

    assert not out.exists()
    assert "budget" in capsys.readouterr().err.lower()
    assert len(llm_env.requests) == 1


def test_llm_aborts_when_the_llm_tier_did_not_run(tmp_path, llm_env, capsys):
    # An empty answer makes the store fall through to the span tier silently
    llm_env.response = _response(text="")
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3

    assert not out.exists()
    err = capsys.readouterr().err
    assert "LLM tier did not run" in err
    assert "S01" in err


def test_llm_aborts_on_a_refusal(tmp_path, llm_env, capsys):
    # Claude Haiku 5.5 has no server-side fallback: a refusal is a text-less response
    llm_env.response = SimpleNamespace(content=[], stop_reason="refusal", usage=_usage())
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3

    assert not out.exists()
    assert "LLM tier did not run" in capsys.readouterr().err


def test_llm_voids_the_run_when_a_retry_falls_back_to_the_span_tier(tmp_path, llm_env, capsys):
    # Attempt 1 rewrites S01 into text that still trips CREDIT-001 (45-day account);
    # attempt 2 gets an empty answer, so the store falls back to the span tier after
    # a genuine llm edit: the scenario carries both tiers and the file must not be written
    llm_env.responses = [
        _response(text="I can offer you a $200 credit right away."),
        _response(text=""),
    ]
    out = tmp_path / "replay_llm.json"

    assert record_replay.main(["--llm", "--out", str(out)]) == 3

    assert not out.exists()
    err = capsys.readouterr().err
    assert "LLM tier did not run" in err
    assert "S01" in err


async def test_cost_guard_prices_usage_and_counts_calls():
    stub = StubClient(_response(usage=_usage(1200, 300)))
    guard = record_replay.CostGuard(stub)

    response = await guard.messages.create(model="m", max_tokens=8192, messages=[])

    assert response is stub.response
    assert guard.calls == 1
    assert guard.input_tokens == 1200
    assert guard.output_tokens == 300
    assert guard.usd == pytest.approx((1200 * 0.10 + 300 * 0.50) / 1e6)
    assert guard.tripped is False
    assert guard.errors == []


async def test_cost_guard_prices_cache_tokens():
    stub = StubClient(_response(usage=_usage(0, 0, cache_creation=1000, cache_read=10_000)))
    guard = record_replay.CostGuard(stub)

    await guard.beta.messages.create(model="m", max_tokens=100, messages=[])

    assert guard.usd == pytest.approx((1000 * 0.125 + 10_000 * 0.01) / 1e6)


async def test_cost_guard_hard_cap_refuses_the_call_before_sending():
    # One priced call lands at $0.999; the next call's worst case (8192 output tokens)
    # would pass $1.00, so it is refused before being sent
    stub = StubClient(_response(usage=_usage(input_tokens=0, output_tokens=1_998_000)))
    guard = record_replay.CostGuard(stub)
    await guard.messages.create(model="m", max_tokens=8192, messages=[])
    assert guard.usd == pytest.approx(0.999)

    with pytest.raises(record_replay.BudgetExceededError):
        await guard.messages.create(model="m", max_tokens=8192, messages=[])

    assert len(stub.requests) == 1
    assert guard.tripped is True


async def test_cost_guard_records_redacted_errors_and_reraises():
    stub = StubClient(error=ValueError(f"bad key {FAKE_KEY} rejected"))
    guard = record_replay.CostGuard(stub)

    with pytest.raises(ValueError):
        await guard.messages.create(model="m", max_tokens=10, messages=[])

    assert guard.errors == ["ValueError: bad key <redacted> rejected"]


def test_build_file_llm_header_is_optional():
    base = record_replay.build_file([], recorded_at="t", git_sha="s", rules=[])
    assert "llm" not in base

    llm = {"model": "claude-haiku-5-5", "calls": 1}
    data = record_replay.build_file(
        [],
        recorded_at="t",
        git_sha="s",
        rules=[],
        mode=record_replay.LLM_MODE,
        latency_note=record_replay.LLM_LATENCY_NOTE,
        llm=llm,
    )
    assert data["mode"] == "llm-recorded"
    assert data["latency_note"] == record_replay.LLM_LATENCY_NOTE
    assert data["llm"] == llm
