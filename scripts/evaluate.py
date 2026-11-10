#!/usr/bin/env python3
"""Run the evaluation framework against the golden dataset.

Usage:
    python scripts/evaluate.py                      # Run full evaluation
    python scripts/evaluate.py --dataset heldout    # Run the blind held-out set
    python scripts/evaluate.py --category pii_leak  # Run specific category
    python scripts/evaluate.py --verbose            # Show each case result
    python scripts/evaluate.py --json               # Output JSON report
    python scripts/evaluate.py --llm-judge          # Run LLM-as-judge scoring
    python scripts/evaluate.py --invariants         # Structural checks on corrected text
    python scripts/evaluate.py --invariants --seeds 3   # ...and the same outputs under 3 hash seeds
    python scripts/evaluate.py --invariants --record-outputs   # rewrite tests/golden_outputs.json

--invariants exits 1 only when a hash-seed run gives different outputs; a failed
check is printed under "Failures" and does not change the exit status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logger = logging.getLogger(__name__)


SEED_TIMEOUT_S = 600
GOLDEN_OUTPUTS = os.path.join(os.path.dirname(__file__), "..", "tests", "golden_outputs.json")


def _child_argv(args: argparse.Namespace) -> list[str]:
    """The command for one hash-seed run, built from the parsed arguments.

    Never from ``sys.argv``: a ``--seeds`` that reached the child would make it
    spawn children of its own.
    """
    argv = [sys.executable, __file__, "--invariants", "--json"]
    if args.category:
        argv += ["--category", args.category]
    if args.case:
        argv += ["--case", args.case]
    return argv


def _seed_run(seed: int, args: argparse.Namespace, digest: str) -> dict:
    """Run the same cases in a subprocess under ``PYTHONHASHSEED=seed`` and compare digests."""
    try:
        child = subprocess.run(
            _child_argv(args),
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            check=False,
            timeout=SEED_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "seed": seed,
            "outputs_sha256": None,
            "same": False,
            "error": f"timed out after {SEED_TIMEOUT_S} s",
        }
    start = child.stdout.find("{")
    try:
        seen = json.loads(child.stdout[start:])["outputs_sha256"] if start >= 0 else None
    except (json.JSONDecodeError, KeyError):
        seen = None
    if seen is None:
        return {
            "seed": seed,
            "outputs_sha256": None,
            "same": False,
            "error": child.stderr.strip()[-2000:] or f"no output (exit {child.returncode})",
        }
    return {"seed": seed, "outputs_sha256": seen, "same": seen == digest}


def _exit_status(seed_runs: list[dict]) -> int:
    """1 only when a hash-seed run gave different outputs or none.

    A failed check is reported under "Failures" and is not fatal.
    """
    return int(any(not run["same"] for run in seed_runs))


async def _run_invariants(dataset, scope: str, kg_store, args: argparse.Namespace) -> int:
    """Print the invariant report and return the exit status (see ``_exit_status``)."""
    from autocorrector.evaluation.invariants import format_invariants, run_invariants

    report = await run_invariants(dataset.cases, kg_store)
    seed_runs = [_seed_run(seed, args, report.digest) for seed in range(1, args.seeds + 1)]

    status = _exit_status(seed_runs)
    recorded = args.record_outputs and status == 0
    if recorded:
        with open(GOLDEN_OUTPUTS, "w", encoding="utf-8") as handle:
            json.dump(report.case_hashes, handle, indent=0, sort_keys=True)
            handle.write("\n")

    if args.json:

        def counts(checks: dict) -> dict:
            return {name: {"k": p.k, "n": p.n} for name, p in checks.items()}

        payload = {
            "scope": scope,
            "corrected": len(report.cases),
            "total_cases": report.total_cases,
            "by_check": counts(report.by_check),
            "by_tier": {tier: counts(checks) for tier, checks in report.by_tier.items()},
            "failures": [
                {"case_id": row.case_id, "tier": row.tier, "notes": list(row.notes)}
                for row in report.cases
                if row.notes
            ],
            "outputs_sha256": report.digest,
            "seeds": seed_runs,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_invariants(report, scope=scope))
        for run in seed_runs:
            verdict = "same outputs" if run["same"] else f"DIFFERENT: {run['outputs_sha256']}"
            print(f"  PYTHONHASHSEED={run['seed']}: {verdict}")
            if run.get("error"):
                print(f"    {run['error']}", file=sys.stderr)
        if args.record_outputs and not recorded:
            print("  not recorded: a hash-seed run differed", file=sys.stderr)
        if recorded:
            print(f"  recorded {len(report.case_hashes)} case hashes in tests/golden_outputs.json")
    return status


async def main() -> None:
    from autocorrector.evaluation.registry import DATASETS, select_dataset

    parser = argparse.ArgumentParser(
        description="Evaluate autocorrector pipeline", allow_abbrev=False
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASETS),
        default="golden",
        help="Dataset to evaluate (default: golden)",
    )
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
        default="claude-sonnet-5-5",
        help="Model for LLM judge (default: claude-sonnet-5-5)",
    )
    parser.add_argument(
        "--invariants",
        action="store_true",
        help="Check corrected text instead of actions (golden set only): idempotence, edit "
        "replay, locality, citations, and a hash of all outputs. A failed check is reported "
        "but the exit status is 1 only when a --seeds run gives different outputs",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=0,
        metavar="N",
        help="With --invariants: re-run in N subprocesses with PYTHONHASHSEED=1..N and "
        "require the same output hash (exit 1 if any differs)",
    )
    parser.add_argument(
        "--record-outputs",
        action="store_true",
        help="With --invariants on the whole golden set: rewrite tests/golden_outputs.json "
        "(one hash per case) after an intended change to the outputs",
    )
    args = parser.parse_args()
    if args.category and args.case:
        parser.error("--category and --case select different cases: give one")
    if args.invariants and args.llm_judge:
        parser.error("--invariants has no model in the loop: drop --llm-judge")
    if args.invariants and args.dataset != "golden":
        # The pinned outputs and the published counts are the golden set's; the
        # held-out set was run once and is never run again
        parser.error("--invariants runs on the golden set only")
    if (args.seeds or args.record_outputs) and not args.invariants:
        parser.error("--seeds and --record-outputs need --invariants")
    if args.seeds < 0:
        parser.error("--seeds must be 0 or more")
    if args.invariants:
        from autocorrector.config import get_settings

        settings = get_settings()
        if settings.anthropic_api_key or settings.openai_api_key or settings.nemo_rails_enabled:
            parser.error(
                "--invariants checks the offline path: unset the API keys "
                "(environment and .env) and turn NeMo rails off"
            )
    if args.record_outputs and (args.category or args.case):
        parser.error("--record-outputs records the whole golden set: drop --category/--case")

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    from autocorrector.evaluation.dataset import GoldenDataset
    from autocorrector.evaluation.runner import run_evaluation
    from autocorrector.evaluation.scorer import format_report
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore

    dataset, scope = select_dataset(args.dataset)

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

    if args.invariants:
        status = await _run_invariants(dataset, scope, kg_store, args)
        await kg_store.close()
        sys.exit(status)

    print(f"\nRunning evaluation: {dataset.name} ({len(dataset.cases)} cases)")
    print(f"Dataset breakdown: {dataset.summary}")
    print(f"Scope: {scope}")
    print()

    report = await run_evaluation(dataset, kg_store=kg_store)

    if args.json:
        print(json.dumps({"scope": scope, **report.model_dump()}, indent=2, default=str))
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
            correction_cases = [c for c in dataset.cases if c.expected_action.value == "correct"]
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
                            judge_cases.append(
                                {
                                    "case_id": case.id,
                                    "original_text": case.agent_text,
                                    "corrected_text": result.corrected_text,
                                    "violations": violations,
                                    "action_taken": "correct",
                                }
                            )
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
