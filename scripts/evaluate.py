#!/usr/bin/env python3
"""Run the evaluation framework against the golden dataset.

Usage:
    python scripts/evaluate.py                      # Run full evaluation
    python scripts/evaluate.py --category pii_leak  # Run specific category
    python scripts/evaluate.py --verbose            # Show each case result
    python scripts/evaluate.py --json               # Output JSON report
    python scripts/evaluate.py --llm-judge          # Run LLM-as-judge scoring
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate autocorrector pipeline")
    parser.add_argument("--category", help="Run only cases in this category")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show each case result")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--case", help="Run a single case by ID")
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Run LLM-as-judge quality scoring on corrections (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--judge-model",
        default="claude-sonnet-4-6",
        help="Model for LLM judge (default: claude-sonnet-4-6)",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    from autocorrector.evaluation.dataset import GOLDEN_DATASET, GoldenDataset
    from autocorrector.evaluation.runner import run_evaluation
    from autocorrector.evaluation.scorer import format_report
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore

    dataset = GOLDEN_DATASET

    # Filter by category or case ID if requested
    if args.category:
        filtered = [c for c in dataset.cases if c.category == args.category]
        if not filtered:
            print(f"No cases found for category '{args.category}'")
            print(f"Available: {list(dataset.by_category.keys())}")
            sys.exit(1)
        dataset = GoldenDataset(
            name=f"{dataset.name} [{args.category}]",
            version=dataset.version,
            cases=filtered,
        )
    elif args.case:
        filtered = [c for c in dataset.cases if c.id == args.case]
        if not filtered:
            print(f"Case '{args.case}' not found")
            sys.exit(1)
        dataset = GoldenDataset(
            name=f"{dataset.name} [{args.case}]",
            version=dataset.version,
            cases=filtered,
        )

    # Set up KG store
    kg_store = KnowledgeGraphStore()
    await kg_store.connect()

    print(f"\nRunning evaluation: {dataset.name} ({len(dataset.cases)} cases)")
    print(f"Dataset breakdown: {dataset.summary}")
    print()

    report = await run_evaluation(dataset, kg_store=kg_store)

    if args.json:
        print(json.dumps(report.model_dump(), indent=2, default=str))
    else:
        print(format_report(report))

    # LLM-as-judge scoring
    if args.llm_judge:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("\nWARNING: ANTHROPIC_API_KEY not set. Skipping LLM judge evaluation.")
        else:
            from autocorrector.evaluation.llm_judge import (
                format_judge_report,
                run_judge_evaluation,
            )
            from autocorrector.orchestrator.pipeline import run_autocorrection

            # Re-run corrections and collect original + corrected text for the judge
            judge_cases = []
            correction_cases = [
                c for c in dataset.cases if c.expected_action.value == "correct"
            ]
            if correction_cases:
                print(f"\nRunning LLM judge on {len(correction_cases)} correction cases...")
                for case in correction_cases:
                    try:
                        result = await run_autocorrection(
                            text=case.agent_text,
                            customer_context=case.customer_context,
                            kg_store=kg_store,
                        )
                        if result.action_taken.value == "correct" and result.corrected_text:
                            violations = [
                                {
                                    "rule_id": v.rule_id,
                                    "description": v.description,
                                    "severity": (
                                        v.severity.value
                                        if hasattr(v.severity, "value")
                                        else str(v.severity)
                                    ),
                                }
                                for v in result.validation.violations
                            ]
                            judge_cases.append({
                                "case_id": case.id,
                                "original_text": case.agent_text,
                                "corrected_text": result.corrected_text,
                                "violations": violations,
                                "action_taken": "correct",
                            })
                    except Exception as e:
                        logger.warning(f"Skipping {case.id} for judge: {e}")

            if judge_cases:
                judge_report = await run_judge_evaluation(
                    judge_cases, api_key=api_key, model=args.judge_model
                )
                print(format_judge_report(judge_report))
            else:
                print("\nNo corrections to judge (no 'correct' actions in results).")

    await kg_store.close()


if __name__ == "__main__":
    asyncio.run(main())
