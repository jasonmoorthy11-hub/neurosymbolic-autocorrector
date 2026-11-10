"""Structural checks on corrected text, with no model in the loop.

The action score says a reply was routed to "correct"; it says nothing about
the text that was sent. These checks do, for every case the pipeline corrects:

- idempotent: the corrected text, checked again, is allowed unchanged.
- replays: the recorded edits, applied to the PII-redacted original, give
  exactly the delivered text, so the edit log accounts for every changed byte.
- local: every sentence that no edit overlaps is delivered byte for byte, in
  the same order. Counted over the cases that have such a sentence; a
  whole-message template (the ``template`` tier) overlaps every sentence.
- cited: every edit names the document and the section of each rule it cites,
  or for a fact fix the document and page of the coverage row whose limit it
  wrote, as ``policies/`` states them.

A correction with no edits (PII redaction only) has nothing to replay, keep or
cite: those three checks are not counted for it, rather than passed.

They are structural: a correction can pass all four and still be the wrong
policy answer. ``record_hashes`` hashes each case's output so a run can be
compared with the recorded one case by case; ``outputs_digest`` is one hash of
all of them, compared across hash seeds and Python versions. The hashed record
is the action, the delivered text, the rule ids found in the original reply, the PII types
and the edits; it leaves out fact errors, the guardrails list, the explanation,
the confidence and the intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocorrector.evaluation.dataset import EvalCase
from autocorrector.evaluation.scorer import Proportion
from autocorrector.guardrails.validators import find_pii_spans, redact_spans
from autocorrector.knowledge_graph.claims import sentence_spans
from autocorrector.knowledge_graph.ingestion import ingest_directory
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionEdit, CorrectionResult
from autocorrector.orchestrator.pipeline import run_autocorrection

FACT_RULE_ID = "FACT-CHECK"
PII_ONLY = "pii-only"
CHECKS = ("idempotent", "replays", "local", "cited")
# The policy documents live at the repository root, not in the installed package
POLICIES_DIR = Path(__file__).resolve().parents[3] / "policies"
_SOURCE_LINE = re.compile(r"\*\*Source:\*\*\s*(\S+), Page (\d+)")


@dataclass(frozen=True)
class CaseInvariants:
    """The four checks for one corrected case; ``tier`` is the edits' tier.

    ``None`` means the check does not apply: no edits (``replays``, ``cited``,
    ``local``), or no sentence left untouched by them (``local``).
    """

    case_id: str
    tier: str
    idempotent: bool
    replays: bool | None
    local: bool | None
    cited: bool | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvariantReport:
    cases: tuple[CaseInvariants, ...]
    digest: str
    total_cases: int
    by_check: dict[str, Proportion] = field(default_factory=dict)
    by_tier: dict[str, dict[str, Proportion]] = field(default_factory=dict)
    case_hashes: dict[str, str] = field(default_factory=dict)


def edit_batches(edits: Sequence[CorrectionEdit]) -> list[list[CorrectionEdit]]:
    """Group edits that index the same text.

    Edits from one stage (the fact fixes, or one policy rewrite) carry offsets
    into the text that stage received, in ascending order. The log does not
    mark where a stage ends, so a new batch starts when the kind of edit
    changes or an edit starts before the previous one ended. A second
    correction attempt whose first edit starts after the previous attempt's
    last edit ended is therefore read as part of that attempt, and a correct
    log then fails to replay.
    """
    batches: list[list[CorrectionEdit]] = []
    for edit in edits:
        current = batches[-1] if batches else None
        same_stage = (
            current is not None
            and (current[-1].rule_id == FACT_RULE_ID) == (edit.rule_id == FACT_RULE_ID)
            and edit.start >= current[-1].end
        )
        if same_stage and current is not None:
            current.append(edit)
        else:
            batches.append([edit])
    return batches


def _sentences(text: str) -> list[tuple[int, int, str]]:
    spans = ((start, end, text[start:end].strip()) for start, end in sentence_spans(text))
    return [span for span in spans if span[2]]


def untouched_sentences(text: str, edits: Sequence[CorrectionEdit]) -> list[str]:
    """Sentences of ``text`` that no edit overlaps, in order.

    A sentence is named by its offsets, not its words: a second "Thanks." stays
    untouched when the first is edited. Its offsets are moved by the edits before
    it, so a later stage's edits are compared in the text that stage received.
    """
    kept = _sentences(text)
    for batch in edit_batches(edits):
        moved = []
        for start, end, sentence in kept:
            if any(edit.start < end and edit.end > start for edit in batch):
                continue
            shift = sum(
                len(edit.after) - (edit.end - edit.start) for edit in batch if edit.end <= start
            )
            moved.append((start + shift, end + shift, sentence))
        kept = moved
    return [sentence for _, _, sentence in kept]


def lost_sentences(untouched: Sequence[str], delivered: str) -> list[str]:
    """The ``untouched`` sentences that ``delivered`` lacks, each needing its own copy in order.

    ``delivered`` is split into sentences again, so a replacement that ends
    without punctuation would merge with the sentence after it and report that
    sentence as lost. The rewriter's replacements all end a sentence.
    """
    sentences = [sentence for _, _, sentence in _sentences(delivered)]
    lost: list[str] = []
    position = 0
    for sentence in untouched:
        try:
            position = sentences.index(sentence, position) + 1
        except ValueError:
            lost.append(sentence)
    return lost


def replay_edits(text: str, edits: Sequence[CorrectionEdit]) -> str | None:
    """Apply the edit log to ``text``; None if an edit's ``before`` is not at its offsets."""
    for batch in edit_batches(edits):
        for edit in reversed(batch):
            if text[edit.start : edit.end] != edit.before:
                return None
            text = text[: edit.start] + edit.after + text[edit.end :]
    return text


class CitationIndex:
    """What ``policies/`` contains, read with the ingestion parser.

    ``rules`` maps a rule id to its (document, section); ``coverage`` holds a
    (document, page, limit) for every coverage row.
    """

    def __init__(self, rules: dict[str, tuple[str, str]], coverage: set[tuple[str, int, str]]):
        self._rules = rules
        self._coverage = coverage

    @classmethod
    async def load(cls, directory: Path = POLICIES_DIR) -> CitationIndex:
        policies = await ingest_directory(str(directory))
        rules = {
            rule.rule_id: (policy.name, rule.section)
            for policy in policies
            for rule in policy.rules
        }
        coverage: set[tuple[str, int, str]] = set()
        for policy in policies:
            for row in policy.coverages:
                # The table row gives the page; the section for that page names the document
                content = (directory / row.source_document).read_text(encoding="utf-8")
                coverage |= {
                    (document, row.source_page, f"${row.limit:,.0f}")
                    for document, page in _SOURCE_LINE.findall(content)
                    if int(page) == row.source_page
                }
        return cls(rules, coverage)

    def resolves(self, edit: CorrectionEdit) -> bool:
        """True when the edit's sources are exactly those of what it cites.

        A fact fix must cite the document and page of a coverage row whose limit
        is the amount it wrote. A rule edit joins its rules' documents and
        sections with "; " and lists a shared one once.
        """
        if not edit.source_document or not edit.source_section:
            return False
        if edit.rule_id == FACT_RULE_ID:
            page = re.fullmatch(r"p\. (\d+)", edit.source_section)
            return bool(page) and (
                (edit.source_document, int(page.group(1)), edit.after) in self._coverage
            )
        sources = [self._rules.get(rule_id) for rule_id in edit.rule_id.split(", ")]
        if not all(sources):
            return False
        # Joined as the rewriter joins them: in rule order, a repeated source once
        documents = list(dict.fromkeys(s[0] for s in sources if s))
        sections = list(dict.fromkeys(s[1] for s in sources if s))
        return (
            edit.source_document.split("; ") == documents
            and edit.source_section.split("; ") == sections
        )


def edits_tier(edits: Sequence[CorrectionEdit]) -> str:
    tiers = sorted({edit.tier for edit in edits})
    return "+".join(tiers) if tiers else PII_ONLY


def _output_record(case_id: str, result: CorrectionResult) -> dict[str, Any]:
    return {
        "id": case_id,
        "action": result.action_taken.value,
        "text": result.corrected_text,
        "rules": [violation.rule_id for violation in result.validation.violations],
        "pii": result.pii_types,
        "edits": [edit.model_dump(mode="json") for edit in result.edits],
    }


def _sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def outputs_digest(records: Sequence[dict[str, Any]]) -> str:
    """SHA-256 over every case's action, delivered text, rule ids, PII types and edits."""
    return _sha256(list(records))


def record_hashes(records: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Case id -> SHA-256 of that case's record, so a difference names its case."""
    return {record["id"]: _sha256(record) for record in records}


async def check_case(
    case: EvalCase,
    result: CorrectionResult,
    store: KnowledgeGraphStore,
    citations: CitationIndex,
) -> CaseInvariants:
    """Run the four checks on one case the pipeline corrected."""
    notes: list[str] = []
    again = await run_autocorrection(
        result.corrected_text, customer_context=case.customer_context, kg_store=store
    )
    idempotent = again.action_taken.value == "allow" and not again.was_modified
    if not idempotent:
        notes.append(f"second pass: {again.action_taken.value}")
    if not result.edits:
        return CaseInvariants(case.id, PII_ONLY, idempotent, None, None, None, tuple(notes))

    redacted = redact_spans(case.agent_text, find_pii_spans(case.agent_text))
    replays = replay_edits(redacted, result.edits) == result.corrected_text
    if not replays:
        notes.append("edit log does not reproduce the delivered text")

    untouched = untouched_sentences(redacted, result.edits)
    lost = lost_sentences(untouched, result.corrected_text)
    if lost:
        notes.append(f"untouched sentence changed: {lost[0]!r}")
    uncited = [edit.rule_id for edit in result.edits if not citations.resolves(edit)]
    if uncited:
        notes.append(f"citation does not resolve: {', '.join(uncited)}")
    return CaseInvariants(
        case_id=case.id,
        tier=edits_tier(result.edits),
        idempotent=idempotent,
        replays=replays,
        local=(not lost) if untouched else None,
        cited=not uncited,
        notes=tuple(notes),
    )


async def run_invariants(
    cases: Sequence[EvalCase],
    store: KnowledgeGraphStore | None = None,
    policies_dir: Path = POLICIES_DIR,
) -> InvariantReport:
    """Run every case, check the corrected ones, and hash all outputs."""
    store = store or KnowledgeGraphStore()
    citations = await CitationIndex.load(policies_dir)
    checked: list[CaseInvariants] = []
    records: list[dict[str, Any]] = []
    for case in cases:
        result = await run_autocorrection(
            case.agent_text, customer_context=case.customer_context, kg_store=store
        )
        records.append(_output_record(case.id, result))
        if result.action_taken.value == "correct":
            checked.append(await check_case(case, result, store, citations))

    def tally(rows: Sequence[CaseInvariants]) -> dict[str, Proportion]:
        tallies = {}
        for check in CHECKS:
            values = [getattr(row, check) for row in rows if getattr(row, check) is not None]
            tallies[check] = Proportion.of(sum(values), len(values))
        return tallies

    tiers = sorted({row.tier for row in checked})
    return InvariantReport(
        cases=tuple(checked),
        digest=outputs_digest(records),
        total_cases=len(cases),
        by_check=tally(checked),
        by_tier={tier: tally([row for row in checked if row.tier == tier]) for tier in tiers},
        case_hashes=record_hashes(records),
    )


def format_invariants(report: InvariantReport, scope: str | None = None) -> str:
    """Plain-text table: each check as passed/counted, then per tier.

    Counts only: the cases are authored and the pipeline is deterministic, so an
    interval would suggest a sample that does not exist. "-" = not counted.
    """

    def cell(p: Proportion) -> str:
        return f"{p.k}/{p.n}" if p.n else "-"

    lines = [
        f"Invariants on corrected text: {len(report.cases)} corrected of"
        f" {report.total_cases} cases (passed/counted)",
    ]
    if scope:
        lines.append(f"Scope: {scope}")
    lines.append("")
    for check in CHECKS:
        lines.append(f"  {check:11} {cell(report.by_check[check])}")
    lines += ["", "  by tier      " + "  ".join(f"{check:>10}" for check in CHECKS)]
    for tier, checks in report.by_tier.items():
        lines.append(f"  {tier:12} " + "  ".join(f"{cell(checks[c]):>10}" for c in CHECKS))
    failures = [row for row in report.cases if row.notes]
    if failures:
        lines += ["", "  Failures:"]
        lines += [f"    {row.case_id} [{row.tier}]: {'; '.join(row.notes)}" for row in failures]
    lines += ["", f"  outputs sha256: {report.digest}"]
    return "\n".join(lines)
