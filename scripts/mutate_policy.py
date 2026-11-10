#!/usr/bin/env python3
"""Mutation score: how much of the default rule set does the golden suite pin down?

Each mutant is the default rule set with one small change (a threshold moved by
one, a condition value moved by one, a keyword dropped, a severity lowered). The
golden cases run against it through the offline pipeline; the mutant is killed
if any case no longer gets its expected action or its expected rule ids. A
surviving mutant marks a part of a rule that no golden case checks.

This measures the test suite, not the pipeline's accuracy. It takes a few
minutes and is not part of CI.

    python3 scripts/mutate_policy.py                 # all mutants
    python3 scripts/mutate_policy.py --rule ESC-001  # one rule
    python3 scripts/mutate_policy.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SEVERITY_DOWN = {"critical": "high", "high": "medium", "medium": "low"}
Rules = dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class Mutant:
    """One change to one rule: ``path`` is a key, or ("conditions", index, key)."""

    rule_id: str
    kind: str
    label: str
    domain: str
    index: int
    path: str | tuple[str, int, str]
    value: Any

    def apply(self, base: Rules) -> Rules:
        """A copy of the rule set with this change made."""
        rules = copy.deepcopy(base)
        rule = rules[self.domain][self.index]
        if isinstance(self.path, tuple):
            key, position, field = self.path
            rule[key][position][field] = self.value
        else:
            rule[self.path] = self.value
        return rules


def generate(base: Rules) -> list[Mutant]:
    """Every mutant of the rule set, in a fixed order."""
    mutants: list[Mutant] = []
    for domain, rules in base.items():
        for index, rule in enumerate(rules):
            rule_id = rule["rule_id"]

            def add(kind: str, label: str, path: Any, value: Any) -> None:
                mutants.append(Mutant(rule_id, kind, label, domain, index, path, value))  # noqa: B023

            if "amount_threshold" in rule:
                for delta in (-1, 1, 10):
                    value = rule["amount_threshold"] + delta
                    add("threshold", f"amount threshold {delta:+d}", "amount_threshold", value)
            for position, condition in enumerate(rule.get("conditions", [])):
                for delta in (-1, 1):
                    add(
                        "condition",
                        f"{condition['field']} {condition['operator']} {condition['value']}"
                        f" moved {delta:+d}",
                        ("conditions", position, "value"),
                        condition["value"] + delta,
                    )
            keywords = rule["keywords"]
            add("keep-one", f"only keyword '{keywords[0]}' kept", "keywords", keywords[:1])
            for keyword in keywords:
                add(
                    "drop-keyword",
                    f"keyword '{keyword}' dropped",
                    "keywords",
                    [k for k in keywords if k != keyword],
                )
            if rule["severity"] in SEVERITY_DOWN:
                lower = SEVERITY_DOWN[rule["severity"]]
                add("severity", f"severity {rule['severity']} -> {lower}", "severity", lower)
    return mutants


async def killer(mutant: Mutant, base: Rules, cases: list[Any]) -> str | None:
    """The id of the first golden case the mutant fails, or None if it survives."""
    from autocorrector.evaluation.runner import evaluate_case
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore()
    store._policy_rules = mutant.apply(base)
    # Cases that expect the mutated rule are the likeliest to fail: run them first
    for case in sorted(cases, key=lambda c: mutant.rule_id not in c.expected_rule_ids):
        result = await evaluate_case(case, store)
        if result.error:
            raise RuntimeError(f"{mutant.rule_id} {mutant.label}: {case.id} raised {result.error}")
        if not (result.is_correct and result.rules_correct):
            return case.id
    return None


async def run(rule_filter: str | None) -> dict[str, Any]:
    from autocorrector.evaluation.dataset import GOLDEN_DATASET
    from autocorrector.evaluation.runner import evaluate_case
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore

    base = KnowledgeGraphStore()._policy_rules
    cases = list(GOLDEN_DATASET.cases)

    # The unmutated rules must pass, or every mutant would look killed
    clean = KnowledgeGraphStore()
    for case in cases:
        result = await evaluate_case(case, clean)
        if not (result.is_correct and result.rules_correct):
            raise SystemExit(f"golden case {case.id} fails on the unmutated rules")

    mutants = [m for m in generate(base) if rule_filter in (None, m.rule_id)]
    start = time.monotonic()
    rows = []
    for number, mutant in enumerate(mutants, 1):
        killed_by = await killer(mutant, base, cases)
        print(
            f"[{number}/{len(mutants)}] {mutant.rule_id} {mutant.label}:"
            f" {'killed by ' + killed_by if killed_by else 'SURVIVED'}",
            file=sys.stderr,
            flush=True,
        )
        rows.append(
            {
                "rule_id": mutant.rule_id,
                "kind": mutant.kind,
                "mutant": mutant.label,
                "killed_by": killed_by,
            }
        )
    killed = sum(1 for row in rows if row["killed_by"])
    return {
        "dataset": f"{GOLDEN_DATASET.name} v{GOLDEN_DATASET.version}",
        "cases": len(cases),
        "mutants": len(rows),
        "killed": killed,
        "survived": len(rows) - killed,
        "score": killed / len(rows) if rows else 0.0,
        "seconds": round(time.monotonic() - start, 1),
        "rows": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Mutation score: {report['dataset']} ({report['cases']} cases)")
    print(
        f"  killed {report['killed']} of {report['mutants']} generated mutants"
        f" ({report['score']:.1%}), {report['seconds']} s"
    )
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for row in report["rows"]:
        by_rule.setdefault(row["rule_id"], []).append(row)
    print("\n  rule          killed")
    for rule_id, rows in by_rule.items():
        killed = sum(1 for row in rows if row["killed_by"])
        print(f"  {rule_id:12}  {killed}/{len(rows)}")
    survivors = [row for row in report["rows"] if not row["killed_by"]]
    print(f"\n  Survivors ({len(survivors)}):")
    for row in survivors:
        print(f"    {row['rule_id']}: {row['mutant']}")


def offline_problems() -> list[str]:
    """Anything that would take the pipeline off the deterministic offline path."""
    from autocorrector.config import get_settings

    settings = get_settings()
    problems = []
    if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("an Anthropic API key is set (the LLM tier would run)")
    if settings.nemo_rails_enabled:
        problems.append("NeMo input rails are enabled")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rule", help="mutate only this rule id")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    problems = offline_problems()
    if problems:
        sys.exit("Refusing to run: " + "; ".join(problems))

    report = asyncio.run(run(args.rule))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    sys.stdout.flush()
    # The audit store's background thread keeps the interpreter alive
    os._exit(0)


if __name__ == "__main__":
    main()
