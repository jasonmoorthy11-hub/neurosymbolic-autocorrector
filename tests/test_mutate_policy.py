"""scripts/mutate_policy.py: the mutant generator and how a mutant is applied."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.knowledge_graph.defaults import load_default_rules

SCRIPT = Path(__file__).parent.parent / "scripts" / "mutate_policy.py"


@pytest.fixture(autouse=True)
def _restore_pipeline_store():
    # killer() installs each mutated store as the pipeline singleton
    from autocorrector.orchestrator import pipeline

    before = pipeline._kg_store
    yield
    pipeline._kg_store = before


def _load():
    spec = importlib.util.spec_from_file_location("mutate_policy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def test_mutate_policy_generates_one_mutant_per_single_change():
    base = load_default_rules()
    mutants = _load().generate(base)
    rules = [rule for domain in base.values() for rule in domain]
    expected = sum(
        3 * ("amount_threshold" in rule)
        + 2 * len(rule.get("conditions", []))
        + 1  # all keywords but the first dropped
        + len(rule["keywords"])
        + (rule["severity"] != "low")
        for rule in rules
    )
    assert len(mutants) == expected
    assert len({(m.rule_id, m.label) for m in mutants}) == len(mutants)


def test_mutate_policy_apply_changes_one_rule_and_leaves_the_base_alone():
    base = load_default_rules()
    module = _load()
    mutant = next(m for m in module.generate(base) if m.label == "keyword 'sue' dropped")
    changed = mutant.apply(base)
    assert "sue" in base["escalation_request"][0]["keywords"]
    assert "sue" not in changed["escalation_request"][0]["keywords"]
    changed["escalation_request"][0]["keywords"].append("sue")
    assert changed == base


def test_mutate_policy_condition_mutant_moves_the_value():
    base = load_default_rules()
    module = _load()
    mutant = next(
        m for m in module.generate(base) if m.rule_id == "REFUND-001" and m.label.endswith("+1")
    )
    assert mutant.apply(base)["refund_processing"][0]["conditions"][0]["value"] == 31


async def test_mutate_policy_killer_names_the_failing_case():
    base = load_default_rules()
    module = _load()
    pins = [c for c in GOLDEN_DATASET.cases if c.id in {"PIN-022", "PIN-023", "PIN-024"}]
    by_label = {m.label: m for m in module.generate(base) if m.rule_id == "REFUND-001"}
    moved_down = by_label["days_since_purchase gt 30 moved -1"]
    moved_up = by_label["days_since_purchase gt 30 moved +1"]
    assert await module.killer(moved_down, base, pins) == "PIN-023"  # day 30 now refused
    assert await module.killer(moved_up, base, pins) == "PIN-024"  # day 31 now allowed
    severity = by_label["severity high -> medium"]
    assert await module.killer(severity, base, pins) is None


def test_golden_pin_rows_carry_exactly_one_keyword_of_their_rule():
    from autocorrector.knowledge_graph.keywords import mentions_keyword

    keywords = {
        rule["rule_id"]: rule["keywords"]
        for domain in load_default_rules().values()
        for rule in domain
    }
    for case in GOLDEN_DATASET.cases:
        if not case.id.startswith("PIN-") or not case.description.startswith("Only keyword"):
            continue
        (rule_id,) = case.expected_rule_ids
        hits = [k for k in keywords[rule_id] if mentions_keyword(case.agent_text, [k])]
        assert len(hits) == 1, (case.id, hits)
