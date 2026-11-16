"""`orchestrator/runner.py` gives what the LangGraph pipeline gives offline."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from autocorrector.config import Settings
from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.rewriter import RewriteResult
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction, CorrectionResult
from autocorrector.multilingual.detector import UNDETERMINED
from autocorrector.orchestrator import runner
from autocorrector.orchestrator.pipeline import run_autocorrection
from autocorrector.orchestrator.runner import is_latin_script, run_deterministic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from record_replay import replay_inputs  # noqa: E402


def _comparable(result: CorrectionResult) -> dict[str, Any]:
    """Everything but the report's random id and its clock reading."""
    return result.model_dump(mode="json", exclude={"explanation": {"correction_id", "timestamp"}})


async def _both(text: str, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = await run_autocorrection(
        text, context, kg_store=KnowledgeGraphStore(), classifier=IntentClassifier()
    )
    plain = await run_deterministic(text, context)
    return _comparable(graph), _comparable(plain)


async def test_runner_matches_the_pipeline_on_every_golden_case():
    different = []
    for case in GOLDEN_DATASET.cases:
        graph, plain = await _both(case.agent_text, case.customer_context)
        if graph != plain:
            different.append(case.id)
    assert different == []
    assert len(GOLDEN_DATASET.cases) == 517


async def test_runner_matches_the_pipeline_on_every_replay_scenario():
    inputs = replay_inputs()
    assert len(inputs) == 25
    for scenario in inputs:
        graph, plain = await _both(scenario.text, scenario.context)
        assert graph == plain, scenario.id


async def test_runner_golden_cases_reach_every_outcome():
    """The parity above is not vacuous: all three actions and a revised reply occur."""
    seen = set()
    for case in GOLDEN_DATASET.cases:
        result = await run_deterministic(case.agent_text, case.customer_context)
        seen.add((result.action_taken, bool(result.edits), result.pii_detected))
    assert {action for action, _, _ in seen} == {
        CorrectionAction.ALLOW,
        CorrectionAction.CORRECT,
        CorrectionAction.ESCALATE,
    }
    assert (CorrectionAction.CORRECT, True, False) in seen
    assert (CorrectionAction.CORRECT, False, True) in seen


# Branches no golden or replay input reaches: both paths get a store patched the
# same way, on a reply with a HIGH violation (CREDIT-001), with and without PII.
_OVER_LIMIT = "I can offer you a $75 credit for your trouble."
_CRITICAL = "I'll apply a $750 credit to your account right away."
_NEW_CUSTOMER = {"account_tenure_days": 30}


def _generation_raises(kg: KnowledgeGraphStore) -> None:
    async def boom(**_: Any):
        raise RuntimeError("no correction available")

    kg.generate_compliant_alternative_with_edits = boom  # type: ignore[method-assign]


def _never_fixes(kg: KnowledgeGraphStore) -> None:
    async def unchanged(*, original_text: str, **_: Any) -> RewriteResult:
        return RewriteResult(text=original_text)

    kg.generate_compliant_alternative_with_edits = unchanged  # type: ignore[method-assign]


def _makes_it_critical(kg: KnowledgeGraphStore) -> None:
    async def worse(**_: Any) -> RewriteResult:
        return RewriteResult(text=_CRITICAL)

    kg.generate_compliant_alternative_with_edits = worse  # type: ignore[method-assign]


def _facts_raise_on_the_second_check(kg: KnowledgeGraphStore) -> None:
    real, calls = kg.verify_facts, []

    async def flaky(**kwargs: Any):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("facts unavailable")
        return await real(**kwargs)

    kg.verify_facts = flaky  # type: ignore[method-assign]


@pytest.mark.parametrize(
    ("patch", "action", "marks"),
    [
        (_generation_raises, CorrectionAction.CORRECT, []),
        (_never_fixes, CorrectionAction.CORRECT, []),
        (_makes_it_critical, CorrectionAction.ESCALATE, []),
        (_facts_raise_on_the_second_check, CorrectionAction.ESCALATE, ["validation-unavailable"]),
    ],
    ids=["generation raises", "max attempts", "critical after a correction", "facts raise later"],
)
@pytest.mark.parametrize("pii", ["", " Your SSN 123-45-6789 is on file."], ids=["no pii", "pii"])
async def test_runner_matches_the_pipeline_on_patched_stores(patch, action, marks, pii):
    text = _OVER_LIMIT + pii
    stores = [KnowledgeGraphStore(), KnowledgeGraphStore()]
    for store in stores:
        patch(store)
    graph = await run_autocorrection(
        text, _NEW_CUSTOMER, kg_store=stores[0], classifier=IntentClassifier()
    )
    plain = await run_deterministic(text, _NEW_CUSTOMER, kg_store=stores[1])
    assert _comparable(plain) == _comparable(graph)
    # and the outcome is the one this branch gives: at the attempt limit the text
    # is delivered as it stands, marked "correct", as the pipeline does
    assert plain.action_taken == action
    assert [m for m in plain.guardrails_applied if m != "pii-detection"] == marks
    assert "123-45-6789" not in plain.corrected_text


def test_runner_defaults_are_the_settings_defaults():
    fields = Settings.model_fields
    assert fields["max_correction_attempts"].default == runner.MAX_CORRECTION_ATTEMPTS
    assert fields["correction_confidence_score"].default == runner.CORRECTION_CONFIDENCE


async def test_runner_check_that_raises_escalates(monkeypatch):
    kg = KnowledgeGraphStore()

    async def boom(**_: Any):
        raise RuntimeError("rules unavailable")

    monkeypatch.setattr(kg, "check_policy_compliance", boom)
    result = await run_deterministic("Thanks for your patience today.", {}, kg_store=kg)
    assert result.action_taken == CorrectionAction.ESCALATE
    assert result.guardrails_applied == ["validation-unavailable"]


@pytest.mark.parametrize(
    ("text", "latin"),
    [
        ("I'll apply a $750 credit to your account right away.", True),
        ("Crédito aplicado, señor Muñoz.", True),
        ("12345 !?", True),
        ("ご注文の商品は明日発送されます。", False),
        ("Ваш заказ отправлен.", False),
        ("Your order مرحبا shipped.", False),
    ],
)
def test_runner_latin_script(text, latin):
    assert is_latin_script(text) is latin


async def test_runner_assume_english_skips_detection(monkeypatch):
    def no_detector(text: str) -> str:
        raise AssertionError("the detector must not run")

    monkeypatch.setattr(runner, "detect_language", no_detector)
    held_without_model = "I'll apply a $750 credit to your account right away."
    result = await run_deterministic(held_without_model, {}, assume_english=True)
    assert result.detected_language == "en"
    assert "translation-unavailable" not in result.guardrails_applied
    assert result.validation.violations  # the rules read it


async def test_runner_assume_english_still_holds_another_script():
    result = await run_deterministic("ご注文の商品は明日発送されます。", {}, assume_english=True)
    assert result.detected_language == UNDETERMINED
    assert result.action_taken == CorrectionAction.ESCALATE
    assert result.guardrails_applied == ["translation-unavailable"]


# What a browser build has: pydantic and cachetools, none of the packages below.
_BROWSER_LIKE = textwrap.dedent(
    """
    import asyncio, importlib.abc, sys

    ABSENT = {"fast_langdetect", "fasttext", "langgraph", "guardrails", "pydantic_settings",
              "dotenv", "aiosqlite", "mcp", "uvicorn", "fastapi", "slowapi", "anthropic",
              "openai", "neo4j", "nemoguardrails", "requests", "httpx"}

    class Absent(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ABSENT:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    sys.meta_path.insert(0, Absent())
    from autocorrector.orchestrator.runner import run_deterministic

    result = asyncio.run(run_deterministic(
        "I can offer you a $75 credit for your trouble.",
        {"account_tenure_days": 30}, assume_english=True))
    assert result.action_taken.value == "correct", result.action_taken
    assert "$50" in result.corrected_text, result.corrected_text
    ours = sorted(m for m in sys.modules if m.startswith("autocorrector"))
    unwanted = [m for m in ours if m.split(".")[1:2] in (["config"], ["audit"], ["dashboard"])
                or m.endswith((".pipeline", ".validators", ".telemetry"))]
    assert not unwanted, unwanted
    print("ok")
    """
)


def test_runner_needs_only_pydantic_and_cachetools():
    done = subprocess.run(
        [sys.executable, "-c", _BROWSER_LIKE], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "ok"
