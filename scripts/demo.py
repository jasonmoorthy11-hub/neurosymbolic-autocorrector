#!/usr/bin/env python3
"""Demo script: Run scenarios through the full autocorrection pipeline.

Demonstrates the core value proposition across 15 scenarios:
- Part 1, scenarios 1-10 (step by step: intent -> PII -> policy/fact checks):
  1-6 credit, facts, compliant, critical credit, PII, PII + policy;
  7 expanded PII (passport, bank account); 8-10 universal rules
  (unauthorized promise, SLA guarantee, internal disclosure)
- Part 2, 5 full LangGraph pipeline runs: unauthorized credit, PII redaction
  (SSN + address), factual error, unauthorized promise, clean output

Usage:
    python scripts/demo.py
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scenarios import PIPELINE_SCENARIOS, STEP_SCENARIOS

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.guardrails.validators import PII_TYPES
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction
from autocorrector.orchestrator.pipeline import run_autocorrection


def print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_field(label: str, value: str, indent: int = 2) -> None:
    prefix = " " * indent
    print(f"{prefix}{label}: {value}")


async def run_scenario(
    kg: KnowledgeGraphStore,
    classifier: IntentClassifier,
    scenario_name: str,
    agent_output: str,
    customer_context: dict,
) -> None:
    print_header(scenario_name)

    print(f'\n  Agent says: "{agent_output}"')
    print(f"  Customer context: {customer_context}")

    # Step 1: Intent Classification
    intent = classifier.classify(agent_output)
    print("\n  [1] Intent Classification:")
    print_field("Intent", intent.intent_type.value, 6)
    print_field("Confidence", f"{intent.confidence:.0%}", 6)
    if intent.extracted_entities:
        print_field("Entities", str(intent.extracted_entities), 6)

    # Step 2: Guardrails AI - PII Detection
    pii_guard = build_pii_guard()
    pii_result = pii_guard.validate(agent_output)
    pii_detected = pii_result.validated_output != agent_output
    print("\n  [2] Guardrails AI - PII Safety Check:")
    if pii_detected:
        print_field("PII DETECTED", "Sensitive data found and redacted", 6)
        print_field("Redacted", f'"{pii_result.validated_output}"', 6)
    else:
        print_field("Result", "No PII detected", 6)

    # Step 3: Policy Compliance
    violations = await kg.check_policy_compliance(
        text=agent_output,
        intent_type=intent.intent_type.value,
        customer_context=customer_context,
    )
    print("\n  [3] Policy Compliance:")
    if violations:
        for v in violations:
            print_field("VIOLATION", f"{v.rule_id} - {v.description}", 6)
            print_field("Severity", v.severity.value, 8)
            print_field("Source", f"{v.source_document}, {v.source_section}", 8)
    else:
        print_field("Result", "COMPLIANT", 6)

    # Step 4: Fact Verification
    fact_errors = await kg.verify_facts(
        text=agent_output,
        customer_context=customer_context,
    )
    inaccurate = [f for f in fact_errors if not f.is_accurate]
    print("\n  [4] Fact Verification:")
    if inaccurate:
        for f in inaccurate:
            print_field("ERROR", f"Claimed {f.claim}, Actual: {f.correct_value}", 6)
            print_field("Source", f"{f.source_document}, Page {f.source_page}", 8)
            print_field("Confidence", f"{f.confidence:.0%}", 8)
    else:
        print_field("Result", "All facts accurate", 6)

    # Step 5: Route through the full pipeline (allow / correct / escalate)
    result = await run_autocorrection(agent_output, customer_context, kg_store=kg)

    if result.action_taken == CorrectionAction.ESCALATE:
        print("\n  [5] Escalated: held for supervisor review, nothing sent.")
        print("\n  Action: ESCALATE (supervisor decides)")
    elif result.action_taken == CorrectionAction.CORRECT:
        print("\n  [5] Autocorrected Output:")
        print(f'      "{result.corrected_text}"')
        for edit in result.edits:
            source = ", ".join(s for s in (edit.source_document, edit.source_section) if s)
            print(f'      {edit.rule_id}: "{edit.before}" -> "{edit.after}" ({source})')

        applied = []
        if result.pii_detected:
            applied.append("PII redaction (" + ", ".join(result.pii_types) + ")")
        if result.validation.violations:
            applied.append("policy compliance")
        if result.validation.fact_errors:
            applied.append("fact correction")
        print(f"\n  Guardrails applied: {', '.join(applied)}")
        print("  Action: CORRECT (workflow continues)")
    else:
        print("\n  [5] No correction needed.")
        print("\n  Action: ALLOW (output passes through)")


async def run_pipeline_scenario(
    kg: KnowledgeGraphStore,
    scenario_name: str,
    agent_output: str,
    customer_context: dict,
) -> None:
    """Run a scenario through the full LangGraph pipeline (the demo's own
    in-memory store, never the settings-built default)."""
    print_header(scenario_name)

    print(f'\n  Agent says: "{agent_output}"')
    print(f"  Customer context: {customer_context}")

    result = await run_autocorrection(
        text=agent_output,
        customer_context=customer_context,
        kg_store=kg,
    )

    print("\n  Pipeline Result:")
    print_field("Action", result.action_taken.value.upper(), 4)
    print_field("Modified", str(result.was_modified), 4)
    print_field("Intent", f"{result.intent.intent_type.value} ({result.intent.confidence:.0%})", 4)
    if result.pii_detected:
        print_field("PII Detected", "YES - redacted before delivery", 4)
    if result.guardrails_applied:
        print_field("Guardrails", ", ".join(result.guardrails_applied), 4)
    if result.validation.violations:
        for v in result.validation.violations:
            print_field("Violation", f"{v.rule_id}: {v.description}", 4)
    if result.validation.fact_errors:
        for f in result.validation.fact_errors:
            print_field("Fact Error", f"Claimed {f.claim}, correct: {f.correct_value}", 4)
    if result.was_modified:
        print("\n  Corrected output:")
        print(f'      "{result.corrected_text}"')
    else:
        print("\n  Output passed through unchanged.")


async def main():
    print("\n" + "=" * 70)
    print("  NEUROSYMBOLIC AUTOCORRECTOR - Live Demo")
    print("  Personal open-source project")
    print("=" * 70)

    kg = KnowledgeGraphStore()
    await kg.connect()
    classifier = IntentClassifier()

    print("\n" + "-" * 70)
    print("  PART 1: Step-by-Step Component Demo")
    print("-" * 70)

    for scenario in STEP_SCENARIOS:
        await run_scenario(kg, classifier, scenario.name, scenario.text, scenario.context)

    print("\n" + "-" * 70)
    print("  PART 2: Full LangGraph Pipeline Demo")
    print("-" * 70)

    for scenario in PIPELINE_SCENARIOS:
        await run_pipeline_scenario(kg, scenario.name, scenario.text, scenario.context)

    rule_ids = sorted(kg.rule_index())
    await kg.close()

    print(f"\n{'=' * 70}")
    print("  Demo complete. All scenarios processed.")
    print("  Components: Intent Classifier + Knowledge Graph + Guardrails AI + LangGraph")
    print(f"  Rules: {len(rule_ids)} ({', '.join(rule_ids)})")
    print(f"  PII types: {len(PII_TYPES)} ({', '.join(PII_TYPES)})")
    print(f"  Evaluation: {len(GOLDEN_DATASET.cases)} golden cases; run scripts/evaluate.py")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    asyncio.run(main())
