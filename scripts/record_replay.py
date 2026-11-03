#!/usr/bin/env python3
"""Record offline pipeline runs for the backend-free replay dashboard.

Runs the 15 demo scenarios (scripts/scenarios.py) and the 10 dashboard demo
scenarios through run_autocorrection with the in-memory knowledge graph, and
writes dashboard/public/replay/replay.json. No API key is used: the script
refuses to run while one is set, so every recorded correction comes from the
deterministic offline path.

Usage:
    python3 scripts/record_replay.py            # write the replay file
    python3 scripts/record_replay.py --check    # exit 1 if the file is stale
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scenarios import PIPELINE_SCENARIOS, STEP_SCENARIOS

from autocorrector.config import get_settings
from autocorrector.dashboard.api import DEMO_SCENARIOS, rule_catalog
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction, CorrectionResult
from autocorrector.orchestrator.pipeline import run_autocorrection

ROOT = Path(__file__).resolve().parent.parent
REPLAY_PATH = ROOT / "dashboard" / "public" / "replay" / "replay.json"
SCHEMA_VERSION = 1
MODE = "offline-deterministic"
KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "AUTOCORRECTOR_ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AUTOCORRECTOR_OPENAI_API_KEY",
)
AGENTS = ("agent-mike", "agent-sarah", "agent-james", "agent-lisa")
SPACING_MS = 2500
WARMUP_TEXT = "Thanks for your patience while I check this."
LATENCY_NOTE = "wall time per scenario on the recording machine, after one untimed warm-up run"
# Fields that differ between runs of the same code; --check ignores them
RUN_SPECIFIC = ("recorded_at", "git_sha", "python")
SCENARIO_RUN_SPECIFIC = ("latency_ms",)


class ReplayInput(NamedTuple):
    id: str
    title: str
    agent_id: str
    text: str
    context: dict[str, Any]


def replay_inputs() -> list[ReplayInput]:
    """The 15 demo scenarios followed by the 10 dashboard demo scenarios."""
    demo = [
        (s.name, AGENTS[i % len(AGENTS)], s.text, s.context)
        for i, s in enumerate(STEP_SCENARIOS + PIPELINE_SCENARIOS)
    ]
    dashboard = [(d["title"], d["agent_id"], d["text"], d["context"]) for d in DEMO_SCENARIOS]
    return [
        ReplayInput(f"S{n:02d}", title, agent, text, context)
        for n, (title, agent, text, context) in enumerate(demo + dashboard, 1)
    ]


def scenario_record(
    inp: ReplayInput, result: CorrectionResult, latency_ms: float, t_offset_ms: int
) -> dict[str, Any]:
    escalated = result.action_taken == CorrectionAction.ESCALATE
    explanation = result.explanation
    return {
        "id": inp.id,
        "title": inp.title,
        "agent_id": inp.agent_id,
        "input": inp.text,
        "customer_context": inp.context,
        "detected_language": result.detected_language,
        "intent": result.intent.intent_type.value,
        "intent_confidence": result.intent.confidence,
        "pii_types": list(result.pii_types),
        "violations": [
            {
                "rule_id": v.rule_id,
                "rule_name": v.rule_name,
                "description": v.description,
                "severity": v.severity.value,
                "source_document": v.source_document,
                "source_section": v.source_section,
            }
            for v in result.validation.violations
        ],
        "fact_errors": [
            {
                "claim": f.claim,
                "correct_value": f.correct_value,
                "source_document": f.source_document,
                "source_page": f.source_page,
            }
            for f in result.validation.fact_errors
        ],
        "edits": [e.model_dump(mode="json") for e in result.edits],
        "action": result.action_taken.value,
        "was_modified": result.was_modified,
        # An escalation sends nothing: the supervisor decides
        "corrected_text": None if escalated else result.corrected_text,
        "severity": result.validation.severity.value,
        "heuristic_confidence": result.confidence,
        "guardrails_applied": list(result.guardrails_applied),
        "explanation": None
        if explanation is None
        else {
            "rationale": explanation.rationale,
            "rules_applied": list(explanation.rules_applied),
            "source_documents": list(explanation.source_documents),
        },
        "latency_ms": round(latency_ms, 1),
        "t_offset_ms": t_offset_ms,
    }


async def record(inputs: list[ReplayInput], kg: KnowledgeGraphStore) -> list[dict[str, Any]]:
    # Untimed warm-up on a non-scenario text (no cache hits): the first call pays
    # one-off lazy initialisation (LATENCY_NOTE)
    await run_autocorrection(WARMUP_TEXT, {}, kg_store=kg)
    records = []
    for i, inp in enumerate(inputs):
        start = time.perf_counter()
        result = await run_autocorrection(inp.text, inp.context, agent_id=inp.agent_id, kg_store=kg)
        latency_ms = (time.perf_counter() - start) * 1000
        records.append(scenario_record(inp, result, latency_ms, i * SPACING_MS))
    return records


def build_file(
    scenarios: list[dict[str, Any]],
    recorded_at: str,
    git_sha: str,
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": recorded_at,
        "git_sha": git_sha,
        "mode": MODE,
        "python": platform.python_version(),
        "latency_note": LATENCY_NOTE,
        # Illustrative ROI constants (Settings defaults), not measured costs
        "roi_assumptions": {
            "cost_per_violation": settings.cost_per_violation,
            "cost_per_escalation": settings.cost_per_escalation,
        },
        "rules": rules,
        "scenarios": scenarios,
    }


def _git_sha() -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        sha = git("rev-parse", "--short", "HEAD")
        return f"{sha}-dirty" if git("status", "--porcelain") else sha
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def _record_all() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kg = KnowledgeGraphStore()
    await kg.connect()
    try:
        return await record(replay_inputs(), kg), rule_catalog(kg)
    finally:
        await kg.close()


def _comparable(data: dict[str, Any]) -> str:
    stripped = {k: v for k, v in data.items() if k not in RUN_SPECIFIC}
    stripped["scenarios"] = [
        {k: v for k, v in s.items() if k not in SCENARIO_RUN_SPECIFIC}
        for s in data.get("scenarios", [])
    ]
    return json.dumps(stripped, indent=2, sort_keys=True, ensure_ascii=False)


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _offline_problems() -> list[str]:
    """Anything that could take the pipeline off the offline path (env or .env)."""
    settings = get_settings()
    problems = [f"{var} is set" for var in KEY_VARS if os.environ.get(var)]
    if settings.anthropic_api_key and "AUTOCORRECTOR_ANTHROPIC_API_KEY is set" not in problems:
        problems.append("an Anthropic key is configured (.env)")
    if settings.openai_api_key and "AUTOCORRECTOR_OPENAI_API_KEY is set" not in problems:
        problems.append("an OpenAI key is configured (.env)")
    if settings.nemo_rails_enabled:
        problems.append("NeMo input rails are enabled")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record offline replay data for the dashboard")
    parser.add_argument("--out", type=Path, default=REPLAY_PATH, help="replay file path")
    parser.add_argument(
        "--check", action="store_true", help="re-record and exit 1 if the file differs"
    )
    args = parser.parse_args(argv)

    problems = _offline_problems()
    if problems:
        print(
            f"Refusing to record: {'; '.join(problems)}. The replay must come from the "
            "offline deterministic path; unset these (environment or .env) and rerun.",
            file=sys.stderr,
        )
        return 2

    scenarios, rules = asyncio.run(_record_all())
    fresh = build_file(
        scenarios,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        git_sha=_git_sha(),
        rules=rules,
    )

    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist; run scripts/record_replay.py", file=sys.stderr)
            return 1
        try:
            current = _comparable(json.loads(args.out.read_text()))
        except json.JSONDecodeError:
            print(f"{args.out} is not valid JSON; run scripts/record_replay.py", file=sys.stderr)
            return 1
        expected = _comparable(fresh)
        if current == expected:
            print(f"{args.out}: up to date ({len(fresh['scenarios'])} scenarios)")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(),
            expected.splitlines(),
            fromfile=str(args.out),
            tofile="re-recorded",
            lineterm="",
        )
        print("\n".join(diff))
        print(f"\n{args.out} is stale; run scripts/record_replay.py", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_dump(fresh))
    print(f"Wrote {len(fresh['scenarios'])} scenarios to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
