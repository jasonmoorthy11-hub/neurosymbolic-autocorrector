#!/usr/bin/env python3
"""False-positive audit on human-written agent turns, and a JSONL batch runner.

Dataset: ABCD v1.1 (Chen et al., 2021, https://github.com/asappresearch/abcd, MIT
licence), pinned to one commit and downloaded at runtime into .cache/abcd/
(gitignored; the data is never committed). ABCD is 10,042 role-played customer
service chats for a fictional retailer: written by people, not production traffic,
and under that retailer's policies, not this project's.

Every agent turn of the dev and test splits (19,144 turns) goes through the
offline pipeline under three stated customer contexts. A turn is "flagged" when the
action is not allow. The script reports the flag rate and writes the flagged turns
for adjudication; it does not judge them (the rubric is in docs/evaluation.md,
written before the first run).

    python3 scripts/bench_real_turns.py --input turns.jsonl   # any JSONL with a "text" field
                                                              # (optional "id", "context")
    python3 scripts/bench_real_turns.py --flagged .private/abcd_flagged.jsonl
        # ABCD dev + test, all contexts. The registered run is published in
        # docs/evaluation.md; the flagged file holds ABCD text, keep it out of git.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
import sys
import time
import urllib.request
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ABCD_COMMIT = "6b8700ce67c6b37b062dd7a60abc76d7ef832a97"
ABCD_URL = (
    f"https://raw.githubusercontent.com/asappresearch/abcd/{ABCD_COMMIT}/data/abcd_v1.1.json.gz"
)
ABCD_SHA256 = "2bdf53ac359543dcdc38d55bc6513e78df120363f8f44870716e909f4606de15"
CACHE = Path(__file__).resolve().parent.parent / ".cache/abcd/abcd_v1.1.json.gz"
PROGRESS_EVERY = 2000
# Three stated contexts. "missing" is what the pipeline assumes when it is told
# nothing (a missing field counts as 0: a new account, a purchase today).
# "established" is a long-standing customer inside the 30-day refund window;
# "late_purchase" is the same customer outside it, the only one of the three in
# which REFUND-001 can fire. None names a plan, so coverage fact checks never run.
CONTEXTS: dict[str, dict[str, Any]] = {
    "missing": {},
    "established": {"account_tenure_days": 365, "days_since_purchase": 10},
    "late_purchase": {"account_tenure_days": 365, "days_since_purchase": 45},
}
VALIDATION_UNAVAILABLE = "validation-unavailable"
# Agent turns per split of the pinned archive (counted without reading them)
EXPECTED_TURNS = {"train": 75985, "dev": 9600, "test": 9544}
# Made-up turns run before the archive is read: each must be flagged for exactly
# the given reason in exactly the given contexts, or the run does not start.
# They catch a detector that is silently off (a language model that failed to
# load falls back to English without an error).
CANARIES = {
    "Muchas gracias por esperar, ya revisamos su pedido y todo está en orden.": (
        "language:es",
        ("missing", "established", "late_purchase"),
    ),
    "Your SSN is 123-45-6789.": ("pii:ssn", ("missing", "established", "late_purchase")),
    "I will process your refund right away.": ("REFUND-001", ("late_purchase",)),
    "Thanks for waiting, your order is on its way.": ("", ()),
}


def fetch(cache: Path) -> None:
    """Download the pinned ABCD archive once."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ABCD_URL, headers={"User-Agent": "bench-real-turns"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 (fixed https URL)
        data = response.read()
    tmp = cache.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(cache)


def verify_archive(cache: Path, expected: str | None = None) -> None:
    """Stop unless the archive is the pinned one: the turn ids index into it."""
    expected = expected or ABCD_SHA256
    actual = hashlib.sha256(cache.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"{cache}: sha256 {actual} is not the pinned ABCD v1.1 archive ({expected}). "
            "Delete it or run with --refresh."
        )


def abcd_turns(cache: Path, splits: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Agent turns of the given ABCD splits: {"id": "<split>-<convo_id>-<turn index>", "text"}.

    The index counts every entry of the conversation (customer turns and actions
    too), so an id points at one entry of the pinned archive.
    """
    with gzip.open(cache, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    for split in dict.fromkeys(splits):
        for conversation in data[split]:
            for index, (speaker, text) in enumerate(conversation["original"]):
                if speaker == "agent":
                    yield {"id": f"{split}-{conversation['convo_id']}-{index}", "text": text}


def jsonl_turns(path: Path) -> Iterator[dict[str, Any]]:
    """Turns from a JSONL file: each line needs "text"; "id" and "context" are optional."""
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                row = None
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise SystemExit(
                    f'{path}:{number}: each line must be a JSON object with a "text" string'
                )
            if not isinstance(row.get("context", {}), dict):
                raise SystemExit(f'{path}:{number}: "context" must be an object')
            turn = {"id": str(row.get("id", number)), "text": row["text"]}
            if "context" in row:
                turn["context"] = row["context"]
            yield turn


def wilson(k: int, n: int) -> str:
    from autocorrector.evaluation.scorer import wilson_interval

    if n == 0:
        return "n/a (0/0)"
    low, high = wilson_interval(k, n)
    return f"{k / n:.2%} ({k}/{n}, 95% CI {low:.2%}-{high:.2%})"


def _flag_record(text: str, result: Any) -> dict[str, Any] | None:
    """What adjudication needs about one run the pipeline did not allow; None if allowed."""
    from autocorrector.guardrails.validators import find_pii_spans

    if result.action_taken.value == "allow":
        return None
    return {
        "action": result.action_taken.value,
        "rule_ids": sorted(v.rule_id for v in result.validation.violations),
        "fact_errors": [
            {"claim": f.claim, "correct_value": f.correct_value}
            for f in result.validation.fact_errors
        ],
        "pii_types": result.pii_types,
        # Spans in the original text: a whole-message template erases the redaction tokens
        "pii_spans": [
            {"type": ptype, "start": start, "end": end, "text": text[start:end]}
            for ptype, start, end in find_pii_spans(text)
        ],
        "language": result.detected_language,
        "guardrails_applied": list(result.guardrails_applied),
        "edits": [edit.model_dump(mode="json") for edit in result.edits],
        "corrected_text": result.corrected_text,
    }


def _error_record(exc: Exception) -> dict[str, Any]:
    """A run that raised: flagged, and labelled false by the protocol."""
    return {
        "action": "error",
        "rule_ids": [],
        "fact_errors": [],
        "pii_types": [],
        "pii_spans": [],
        "language": "en",
        "guardrails_applied": [],
        "edits": [],
        "corrected_text": "",
        "error": f"{type(exc).__name__}: {exc}"[:500],
    }


async def run_turns(
    turns: list[dict[str, Any]], contexts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """One record per (turn, context) the pipeline did not allow.

    Identical texts are run once per context and the result reused. A run that
    raises becomes a record with action "error" instead of ending the audit.
    """
    from autocorrector.knowledge_graph.store import KnowledgeGraphStore
    from autocorrector.orchestrator.pipeline import run_autocorrection

    store = KnowledgeGraphStore()
    flagged: list[dict[str, Any]] = []
    done = 0
    for name, base_context in contexts.items():
        seen: dict[str, dict[str, Any] | None] = {}
        for turn in turns:
            done += 1
            if done % PROGRESS_EVERY == 0:
                total = len(turns) * len(contexts)
                print(f"  {done}/{total} runs, {len(flagged)} flagged", file=sys.stderr, flush=True)
            context = {**base_context, **turn.get("context", {})}
            key = json.dumps([turn["text"], context], sort_keys=True)
            if key not in seen:
                try:
                    result = await run_autocorrection(
                        turn["text"], customer_context=context, kg_store=store
                    )
                    seen[key] = _flag_record(turn["text"], result)
                except Exception as exc:  # noqa: BLE001 (one bad turn must not lose the run)
                    seen[key] = _error_record(exc)
            if seen[key] is not None:
                flagged.append(
                    {"id": turn["id"], "context": name, "text": turn["text"], **seen[key]}
                )
    return flagged


def reason(record: dict[str, Any]) -> str:
    """Everything that made the pipeline act, as one label per flagged record.

    Rule ids sorted, then FACT-CHECK, the types of the redacted PII spans (not
    ``pii_types``, which can name a type whose match another type's redaction
    already covered), a detected language other than English (always, since it
    alone escalates) and a failed validation.
    """
    if record["action"] == "error":
        return "error"
    parts = sorted(record["rule_ids"])
    if record["fact_errors"]:
        parts.append("FACT-CHECK")
    parts += [f"pii:{t}" for t in dict.fromkeys(span["type"] for span in record["pii_spans"])]
    if record["language"] != "en":
        parts.append(f"language:{record['language']}")
    if VALIDATION_UNAVAILABLE in record.get("guardrails_applied", []):
        parts.append(VALIDATION_UNAVAILABLE)
    return "+".join(parts) or "other"


def flagged_digest(flagged: list[dict[str, Any]]) -> str:
    """SHA-256 over (id, context, action, reason) of every flagged record."""
    rows = sorted((r["id"], r["context"], r["action"], reason(r)) for r in flagged)
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


async def preflight() -> None:
    """Exit unless every canary is flagged as expected (see ``CANARIES``)."""
    turns = [{"id": str(n), "text": text} for n, text in enumerate(CANARIES)]
    flagged = await run_turns(turns, CONTEXTS)
    for text, (expected, contexts) in CANARIES.items():
        got = {r["context"]: reason(r) for r in flagged if r["text"] == text}
        if got != dict.fromkeys(contexts, expected):
            raise SystemExit(
                f"Preflight failed, nothing was run on the data: {text!r} gave {got}, "
                f"expected {expected!r} in {list(contexts)}"
            )


def git_state() -> str:
    """The commit the run is made at, and whether tracked files differ from it."""
    import subprocess

    root = Path(__file__).resolve().parent.parent

    def git(*argv: str) -> str:
        done = subprocess.run(
            ["git", *argv], cwd=root, capture_output=True, text=True, check=False, timeout=30
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    commit = git("rev-parse", "HEAD") or "unknown"
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return f"{commit}{' (with uncommitted changes)' if dirty else ''}"


def environment() -> str:
    """Versions and settings that can change the result, for the report header."""
    from importlib.metadata import PackageNotFoundError, version

    from autocorrector import __version__
    from autocorrector.config import get_settings

    try:
        langdetect = version("fast-langdetect")
    except PackageNotFoundError:
        langdetect = "not installed"
    settings = get_settings()
    return (
        f"commit {git_state()}\n"
        f"autocorrector {__version__}, Python {sys.version.split()[0]}, "
        f"fast-langdetect {langdetect}, max_correction_attempts "
        f"{settings.max_correction_attempts}, supported_languages {settings.supported_languages}"
    )


def print_report(
    source: str, turns: list[dict[str, Any]], flagged: list[dict[str, Any]], contexts: Iterable[str]
) -> None:
    unique = len({turn["text"] for turn in turns})
    print(f"# Real-turn audit: {source}")
    print(environment())
    print(f"Agent turns: {len(turns)} ({unique} distinct texts)")
    for name in contexts:
        rows = [record for record in flagged if record["context"] == name]
        print(f"\n## Context: {name}")
        print(f"  flagged turns   {wilson(len(rows), len(turns))}")
        print(f"  distinct texts  {wilson(len({r['text'] for r in rows}), unique)}")
        for action, count in sorted(Counter(r["action"] for r in rows).items()):
            print(f"    {action:9s} {count}")
        print("  by reason:")
        for label, count in Counter(reason(r) for r in rows).most_common():
            print(f"    {count:5d}  {label}")
    print(f"\nflagged sha256: {flagged_digest(flagged)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split", nargs="+", choices=["train", "dev", "test"], default=["dev", "test"]
    )
    parser.add_argument("--input", type=Path, help="run a JSONL file instead of ABCD")
    parser.add_argument(
        "--flagged",
        type=Path,
        help="write the flagged turns here as JSONL (it holds the turn text: keep it out of git)",
    )
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--refresh", action="store_true", help="re-download ABCD")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)

    from autocorrector.config import get_settings

    settings = get_settings()
    keys = (
        settings.anthropic_api_key,
        settings.openai_api_key,
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
    )
    if any(keys):
        sys.exit("Refusing to run: an API key is set (a model could be called)")
    if settings.nemo_rails_enabled:
        sys.exit("Refusing to run: NeMo input rails are enabled")

    if args.input:
        source = str(args.input)
        turns = list(jsonl_turns(args.input))
        contexts = {"given": {}}
    else:
        if args.refresh or not args.cache.exists():
            print(f"Downloading ABCD v1.1 ({ABCD_COMMIT[:7]}) to {args.cache} ...", file=sys.stderr)
            fetch(args.cache)
        verify_archive(args.cache)
        asyncio.run(preflight())
        splits = list(dict.fromkeys(args.split))
        source = f"ABCD v1.1 @ {ABCD_COMMIT[:7]}, {' + '.join(splits)}, agent turns"
        turns = list(abcd_turns(args.cache, splits))
        expected = sum(EXPECTED_TURNS[split] for split in splits)
        if len(turns) != expected:
            sys.exit(f"Expected {expected} agent turns in {splits}, found {len(turns)}")
        contexts = CONTEXTS

    # Opened before the run: a bad path must not cost a second run
    out = args.flagged.open("w", encoding="utf-8") if args.flagged else None
    try:
        started = time.monotonic()
        flagged = asyncio.run(run_turns(turns, contexts))
        if out:  # before the report, so a report failure cannot lose the records
            for record in flagged:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
        print_report(source, turns, flagged, contexts)
        print(f"({time.monotonic() - started:.0f} s)")
        if out:
            print(f"Flagged turns written to {args.flagged}")
    finally:
        if out:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
