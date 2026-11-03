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

from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.claims import apply_fact_corrections
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
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

    # Step 5: Correction
    if pii_detected or violations or inaccurate:
        # Start with PII-redacted text if needed
        corrected = pii_result.validated_output if pii_detected else agent_output

        # Apply fact corrections (deterministic replacement of the wrong amounts)
        corrected = apply_fact_corrections(corrected, inaccurate)

        # Apply policy corrections
        if violations:
            corrected = await kg.generate_compliant_alternative(
                original_text=corrected,
                violations=[v.model_dump() for v in violations],
                intent_type=intent.intent_type.value,
                customer_context=customer_context,
            )

        print("\n  [5] Autocorrected Output:")
        print(f'      "{corrected}"')

        applied = []
        if pii_detected:
            applied.append("PII redaction")
        if violations:
            applied.append("policy compliance")
        if inaccurate:
            applied.append("fact correction")
        print(f"\n  Guardrails applied: {', '.join(applied)}")
        print("  Action: CORRECT (workflow continues)")
    else:
        print("\n  [5] No correction needed.")
        print("\n  Action: ALLOW (output passes through)")


async def run_pipeline_scenario(
    scenario_name: str,
    agent_output: str,
    customer_context: dict,
) -> None:
    """Run a scenario through the full LangGraph pipeline."""
    print_header(scenario_name)

    print(f'\n  Agent says: "{agent_output}"')
    print(f"  Customer context: {customer_context}")

    result = await run_autocorrection(
        text=agent_output,
        customer_context=customer_context,
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

    # Scenario 1: Credit Approval
    await run_scenario(
        kg,
        classifier,
        "Scenario 1: Credit Approval (Financial Exposure Control)",
        "I can offer you a $200 credit for the inconvenience.",
        {"account_tenure_days": 45, "customer_id": "CUST-001", "account_tier": "standard"},
    )

    # Scenario 2: Policy Explanation
    await run_scenario(
        kg,
        classifier,
        "Scenario 2: Policy Explanation (Hallucination Prevention)",
        "Your plan covers up to $5,000 in emergency dental work.",
        {"plan_type": "standard_2024", "customer_id": "CUST-002"},
    )

    # Scenario 3: Compliant output
    await run_scenario(
        kg,
        classifier,
        "Scenario 3: Compliant Output (No Correction Needed)",
        "I understand your concern. Let me look into this for you right away.",
        {"account_tenure_days": 200, "plan_type": "premium_2024"},
    )

    # Scenario 4: Critical violation
    await run_scenario(
        kg,
        classifier,
        "Scenario 4: Critical Violation (Excessive Credit)",
        "I'll apply a $1,000 credit to your account immediately.",
        {"account_tenure_days": 30},
    )

    # Scenario 5: PII Detection (Guardrails AI)
    await run_scenario(
        kg,
        classifier,
        "Scenario 5: PII Detection (Guardrails AI Safety Rail)",
        "I've confirmed your identity. Your SSN 412-55-7890 and email"
        " sarah.jones@gmail.com are on file.",
        {"customer_id": "CUST-003", "account_tenure_days": 365},
    )

    # Scenario 6: PII + Policy Violation
    await run_scenario(
        kg,
        classifier,
        "Scenario 6: PII + Policy Violation (Combined Guardrails)",
        "I'll issue a $300 credit to card 4532-8765-1234-9999."
        " Your SSN 123-45-6789 confirms identity.",
        {"customer_id": "CUST-004", "account_tenure_days": 15},
    )

    # Scenario 7: New PII — Passport + Bank Account
    await run_scenario(
        kg,
        classifier,
        "Scenario 7: Expanded PII Detection (Passport + Bank Account)",
        "For the wire transfer: passport number AB1234567, account number: 55667788990.",
        {"customer_id": "CUST-005", "account_tenure_days": 100},
    )

    # Scenario 8: Unauthorized Promise
    await run_scenario(
        kg,
        classifier,
        "Scenario 8: Unauthorized Promise (PROMISE-001)",
        "I promise you'll never be charged that fee again. It's permanently removed.",
        {"customer_id": "CUST-006", "account_tenure_days": 90},
    )

    # Scenario 9: Unauthorized Timeline Guarantee
    await run_scenario(
        kg,
        classifier,
        "Scenario 9: Unauthorized Timeline (SLA-001)",
        "I guarantee this will be resolved within 24 hours.",
        {"customer_id": "CUST-007", "account_tenure_days": 120},
    )

    # Scenario 10: Internal Process Disclosure
    await run_scenario(
        kg,
        classifier,
        "Scenario 10: Internal Disclosure (DISC-001)",
        "Between you and me, our CRM shows your case was flagged as priority.",
        {"customer_id": "CUST-008", "account_tenure_days": 60},
    )

    print("\n" + "-" * 70)
    print("  PART 2: Full LangGraph Pipeline Demo")
    print("-" * 70)

    await run_pipeline_scenario(
        "Pipeline: Unauthorized Credit",
        "I'll go ahead and issue you a $75 credit right now.",
        {"customer_id": "C-5678", "plan_type": "standard_2024", "account_tenure_days": 30},
    )

    await run_pipeline_scenario(
        "Pipeline: PII Redaction (SSN + Address)",
        "Your SSN: 987-65-4321 and address is 123 Main St, Springfield, IL 62704.",
        {"customer_id": "C-9999", "account_tenure_days": 200},
    )

    await run_pipeline_scenario(
        "Pipeline: Factual Error",
        "Your Standard plan covers up to $5,000 in emergency dental procedures.",
        {"customer_id": "C-9012", "plan_type": "standard_2024", "account_tenure_days": 365},
    )

    await run_pipeline_scenario(
        "Pipeline: Unauthorized Promise",
        "I guarantee you'll have lifetime access to this feature forever.",
        {"customer_id": "C-2222", "account_tenure_days": 200},
    )

    await run_pipeline_scenario(
        "Pipeline: Clean Output",
        "I'd be happy to help you with that. Let me check your account details.",
        {"customer_id": "C-1111", "account_tenure_days": 365},
    )

    await kg.close()

    print(f"\n{'=' * 70}")
    print("  Demo complete. All scenarios processed.")
    print("  Components: Intent Classifier + Knowledge Graph + Guardrails AI + LangGraph")
    print("  Rules: 8 (CREDIT x2, REFUND x2, ESC x1, PROMISE x1, SLA x1, DISC x1)")
    print("  PII types: 10 (SSN, card, email, phone, DOB, passport, bank, routing, DL, address)")
    print("  Evaluation: run scripts/evaluate.py for current metrics")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    asyncio.run(main())
