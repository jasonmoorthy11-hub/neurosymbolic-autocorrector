#!/usr/bin/env python3
"""Record offline pipeline runs for the backend-free replay dashboard.

Runs the 15 demo scenarios (scripts/scenarios.py) and the 10 dashboard demo
scenarios through run_autocorrection with the in-memory knowledge graph, and
writes dashboard/public/replay/replay.json. No API key is used: the script
refuses to run while one is set, so every recorded correction comes from the
deterministic offline path.

The one exception is ``--llm``: a single recording of the same scenarios with
the LLM correction tier (Claude Haiku 5.5), written to replay_llm.json. It runs
only with AUTOCORRECTOR_ANTHROPIC_API_KEY set inline, behind a cost guard that
aborts on any SDK error or past the budget, and never in CI.

Usage:
    python3 scripts/record_replay.py            # write the replay file
    python3 scripts/record_replay.py --check    # exit 1 if the file is stale
    python3 scripts/record_replay.py --llm      # the one LLM-tier recording (paid)
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import anthropic
from dotenv import dotenv_values

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

# --llm: the one recorded run with the LLM correction tier
LLM_REPLAY_PATH = ROOT / "dashboard" / "public" / "replay" / "replay_llm.json"
LLM_MODE = "llm-recorded"
LLM_MODEL = "claude-haiku-5-5"
LLM_KEY_VAR = "AUTOCORRECTOR_ANTHROPIC_API_KEY"
# Claude Haiku 5.5 list prices per token (prompts <= 100K); cache reads 0.1x, writes 1.25x
LLM_PRICE_IN = 0.10 / 1e6
LLM_PRICE_OUT = 0.50 / 1e6
LLM_PRICE_CACHE_READ = LLM_PRICE_IN * 0.1
LLM_PRICE_CACHE_WRITE = LLM_PRICE_IN * 1.25
LLM_SOFT_USD = 0.90  # reached after a call: stop before the next scenario
LLM_HARD_USD = 1.00  # a call whose worst case would pass this is never sent
LLM_LATENCY_NOTE = (
    "wall time per scenario on the recording machine with the LLM correction tier; "
    "includes one Claude API round trip per correction attempt for corrected scenarios"
)
# SDK settings that would send the key elsewhere or attach a second credential
LLM_FORBIDDEN_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
FACT_EDIT_RULE_ID = "FACT-CHECK"  # fact-check edits stay on the deterministic span path
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]*")


def _redact(text: str) -> str:
    return _KEY_PATTERN.sub("<redacted>", text)


class BudgetExceededError(RuntimeError):
    """Raised before a call whose worst-case cost would pass LLM_HARD_USD."""


class LLMRecordingError(RuntimeError):
    """The LLM recording must not be written (guard tripped, SDK error, tier missing)."""


class CostGuard:
    """Wraps an AsyncAnthropic client: prices every call, trips on budget or error.

    Exposes ``messages.create`` and ``beta.messages.create`` like the SDK client so
    ``KnowledgeGraphStore._get_anthropic_client`` can return it unchanged. The
    store swallows SDK exceptions, so they are also kept in ``errors`` for the
    recorder to read.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usd = 0.0
        self.tripped = False
        self.errors: list[str] = []
        self.messages = SimpleNamespace(create=self._wrap(lambda: self._client.messages.create))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=self._wrap(lambda: self._client.beta.messages.create))
        )

    def _wrap(self, create: Callable[[], Callable[..., Awaitable[Any]]]) -> Callable[..., Any]:
        async def call(**kwargs: Any) -> Any:
            return await self._call(create(), kwargs)

        return call

    @staticmethod
    def _worst_case_usd(kwargs: dict[str, Any]) -> float:
        # Output at max_tokens plus the prompt text at an over-estimate of 1 token / 3 chars
        prompt_chars = len(str(kwargs.get("system", ""))) + sum(
            len(str(m.get("content", ""))) for m in kwargs.get("messages", [])
        )
        return kwargs["max_tokens"] * LLM_PRICE_OUT + (prompt_chars / 3) * LLM_PRICE_IN

    async def _call(self, create: Callable[..., Awaitable[Any]], kwargs: dict[str, Any]) -> Any:
        if self.usd + self._worst_case_usd(kwargs) > LLM_HARD_USD:
            self.tripped = True
            raise BudgetExceededError(
                f"hard cap ${LLM_HARD_USD:.2f} would be passed (spent ${self.usd:.4f})"
            )
        try:
            response = await create(**kwargs)
        except Exception as e:
            self.errors.append(_redact(f"{type(e).__name__}: {e}"))
            raise
        usage = response.usage
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.usd += (
            usage.input_tokens * LLM_PRICE_IN
            + usage.output_tokens * LLM_PRICE_OUT
            + cache_write * LLM_PRICE_CACHE_WRITE
            + cache_read * LLM_PRICE_CACHE_READ
        )
        if self.usd >= LLM_SOFT_USD:
            self.tripped = True
        return response

    def summary(self) -> dict[str, Any]:
        return {
            "model": LLM_MODEL,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": round(self.usd, 4),
            "recorded_with_sdk": anthropic.__version__,
        }


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


async def record_llm(
    inputs: list[ReplayInput], kg: KnowledgeGraphStore, guard: CostGuard
) -> list[dict[str, Any]]:
    """record() with the LLM tier: fail closed on any guard trip, error or silent fallback."""
    await run_autocorrection(WARMUP_TEXT, {}, kg_store=kg)
    records = []
    for i, inp in enumerate(inputs):
        start = time.perf_counter()
        result = await run_autocorrection(inp.text, inp.context, agent_id=inp.agent_id, kg_store=kg)
        latency_ms = (time.perf_counter() - start) * 1000
        if guard.errors:
            raise LLMRecordingError(f"SDK error during {inp.id}: {'; '.join(guard.errors)}")
        if guard.tripped:
            raise LLMRecordingError(
                f"budget guard tripped during {inp.id}: ${guard.usd:.4f} after {guard.calls} calls"
            )
        if result.detected_language != "en":
            # Translation uses a settings-built client outside the guard; the key is out
            # of the environment, so such a scenario escalates instead, which is not the
            # recording this lane claims to be
            raise LLMRecordingError(
                f"{inp.id} detected as {result.detected_language!r}; only English scenarios "
                "stay on the guarded path"
            )
        records.append(scenario_record(inp, result, latency_ms, i * SPACING_MS))
    # The store swallows SDK exceptions, refusals and empty answers and falls through
    # to the span tier, on the first attempt or on a retry after revalidation: every
    # policy edit of a corrected scenario with violations must be the llm tier
    missing = [
        r["id"]
        for r in records
        if r["action"] == "correct"
        and r["violations"]
        and (
            not any(e["tier"] == "llm" for e in r["edits"])
            or any(e["tier"] != "llm" for e in r["edits"] if e["rule_id"] != FACT_EDIT_RULE_ID)
        )
    ]
    if missing:
        raise LLMRecordingError(f"LLM tier did not run for {', '.join(missing)}")
    return records


def build_file(
    scenarios: list[dict[str, Any]],
    recorded_at: str,
    git_sha: str,
    rules: list[dict[str, Any]],
    mode: str = MODE,
    latency_note: str = LATENCY_NOTE,
    llm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": recorded_at,
        "git_sha": git_sha,
        "mode": mode,
        "python": platform.python_version(),
        "latency_note": latency_note,
        # Illustrative ROI constants (Settings defaults), not measured costs
        "roi_assumptions": {
            "cost_per_violation": settings.cost_per_violation,
            "cost_per_escalation": settings.cost_per_escalation,
        },
    }
    if llm is not None:
        data["llm"] = llm
    data["rules"] = rules
    data["scenarios"] = scenarios
    return data


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


def _make_client(key: str) -> Any:
    """Seam for tests: the real SDK client for the one recording."""
    return anthropic.AsyncAnthropic(api_key=key)


async def _record_all_llm(
    key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], CostGuard]:
    kg = KnowledgeGraphStore(anthropic_api_key=key, correction_llm=LLM_MODEL)
    await kg.connect()
    guard = CostGuard(_make_client(key))
    # The store returns a pre-set client as is (KnowledgeGraphStore._get_anthropic_client)
    kg._anthropic_client = guard
    try:
        return await record_llm(replay_inputs(), kg, guard), rule_catalog(kg), guard
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


def _llm_problems() -> list[str]:
    """The LLM recording runs only with the one key var set inline and nothing else."""
    problems = []
    if not os.environ.get(LLM_KEY_VAR):
        problems.append(f"{LLM_KEY_VAR} is not set")
    others = [v for v in KEY_VARS if v != LLM_KEY_VAR] + list(LLM_FORBIDDEN_VARS)
    problems += [f"{var} is set" for var in others if os.environ.get(var)]
    for env_file in dict.fromkeys((ROOT / ".env", Path.cwd() / ".env")):
        if env_file.is_file():
            values = dotenv_values(env_file)
            defined = [var for var in (*KEY_VARS, *LLM_FORBIDDEN_VARS) if values.get(var)]
            if defined:
                problems.append(f"{env_file} defines {', '.join(defined)}")
    if get_settings().nemo_rails_enabled:
        problems.append("NeMo input rails are enabled")
    return problems


def _record_llm_file(out: Path) -> int:
    problems = _llm_problems()
    if problems:
        print(
            f"Refusing to record the LLM run: {'; '.join(problems)}. Pass the key inline as "
            f"{LLM_KEY_VAR} with every other key unset (environment and .env) and rerun.",
            file=sys.stderr,
        )
        return 2
    # Take the key out of the environment: from here on Settings sees no key, so the
    # pipeline's own clients (translation, a settings-built store) cannot be created
    # and every call goes through the guard injected into the one store below
    key = os.environ.pop(LLM_KEY_VAR)
    try:
        scenarios, rules, guard = asyncio.run(_record_all_llm(key))
    except (LLMRecordingError, BudgetExceededError) as e:
        print(f"LLM recording aborted, nothing written: {_redact(str(e))}", file=sys.stderr)
        return 3
    fresh = build_file(
        scenarios,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        git_sha=_git_sha(),
        rules=rules,
        mode=LLM_MODE,
        latency_note=LLM_LATENCY_NOTE,
        llm=guard.summary(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_dump(fresh))
    print(
        f"Wrote {len(scenarios)} scenarios to {out} "
        f"({guard.calls} LLM calls, ${guard.usd:.4f}, {LLM_MODEL})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record offline replay data for the dashboard")
    parser.add_argument("--out", type=Path, default=None, help="replay file path (default by mode)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="re-record and exit 1 if the file differs"
    )
    mode.add_argument(
        "--llm",
        action="store_true",
        help=f"record the one LLM-tier run ({LLM_MODEL}) to replay_llm.json; paid",
    )
    args = parser.parse_args(argv)

    if args.llm:
        return _record_llm_file(args.out or LLM_REPLAY_PATH)
    if args.out is None:
        args.out = REPLAY_PATH

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
