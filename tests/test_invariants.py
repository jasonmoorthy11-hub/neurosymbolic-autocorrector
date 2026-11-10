"""evaluation/invariants.py: structural checks on corrected text, and the pinned outputs.

The golden-set numbers asserted here are the ones docs/evaluation.md publishes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET, EvalCase, ExpectedAction
from autocorrector.evaluation.invariants import (
    CitationIndex,
    check_case,
    edit_batches,
    format_invariants,
    lost_sentences,
    outputs_digest,
    record_hashes,
    replay_edits,
    run_invariants,
    untouched_sentences,
)
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionEdit
from autocorrector.orchestrator.pipeline import run_autocorrection

REPO = Path(__file__).resolve().parent.parent
OUTPUTS_FILE = Path(__file__).parent / "golden_outputs.json"
RECORD_COMMAND = "python3 scripts/evaluate.py --invariants --record-outputs"

sys.path.insert(0, str(REPO / "scripts"))

import evaluate  # noqa: E402


def _edit(rule_id: str, before: str, after: str, start: int, **kwargs) -> CorrectionEdit:
    return CorrectionEdit(
        rule_id=rule_id, before=before, after=after, start=start, end=start + len(before), **kwargs
    )


class TestReplayEdits:
    def test_edits_of_one_stage_share_their_offsets(self):
        text = "Bad one. Keep me. Bad two."
        edits = [_edit("SLA-001", "Bad one.", "Fixed.", 0), _edit("SLA-001", "Bad two.", "", 18)]
        assert replay_edits(text, edits) == "Fixed. Keep me. "

    def test_a_later_stage_indexes_the_text_the_earlier_one_produced(self):
        text = "Dental is $5,000. I promise."
        fact = _edit("FACT-CHECK", "$5,000", "$2,500", 10)
        policy = _edit("PROMISE-001", "I promise.", "Noted.", 18)
        assert len(edit_batches([fact, policy])) == 2
        assert replay_edits(text, [fact, policy]) == "Dental is $2,500. Noted."

    def test_an_edit_whose_before_is_not_at_its_offsets_fails(self):
        assert replay_edits("Hello there.", [_edit("SLA-001", "Goodbye", "Hi", 0)]) is None

    def test_a_second_attempt_that_starts_before_the_first_ended_is_its_own_batch(self):
        text = "Bad one. Keep me."
        first = _edit("SLA-001", "Bad one.", "Still bad.", 0)
        second = _edit("SLA-001", "Still bad.", "Fixed.", 0)
        assert len(edit_batches([first, second])) == 2
        assert replay_edits(text, [first, second]) == "Fixed. Keep me."

    def test_a_second_attempt_that_starts_later_is_merged_a_known_limit(self):
        """The log does not mark attempts: this correct log is read as one batch and fails.

        Applied in order it gives "All fixed now. Kept."; no golden case has two attempts.
        """
        text = "Bad one. Keep bad."
        first = _edit("SLA-001", "Bad one.", "All fixed now.", 0)
        # Offsets into "All fixed now. Keep bad."
        second = _edit("SLA-001", "Keep bad.", "Kept.", 15)
        assert len(edit_batches([first, second])) == 1
        assert replay_edits(text, [first, second]) is None


class TestUntouchedSentences:
    def test_sentence_outside_every_edit_is_untouched(self):
        text = "Dental is $5,000. Have a nice day."
        assert untouched_sentences(text, [_edit("FACT-CHECK", "$5,000", "$2,500", 10)]) == [
            "Have a nice day."
        ]

    def test_whole_message_edit_names_every_sentence(self):
        text = "One. Two."
        assert untouched_sentences(text, [_edit("DISC-001", text, "Sorry.", 0)]) == []

    def test_sentences_are_named_by_offset_not_by_shared_text(self):
        text = "Dental is $5,000. The ER limit is $5,000 too."
        assert untouched_sentences(text, [_edit("FACT-CHECK", "$5,000", "$2,500", 10)]) == [
            "The ER limit is $5,000 too."
        ]

    def test_a_duplicate_of_an_edited_sentence_stays_untouched(self):
        text = "Thanks. Bad one. Thanks."
        assert untouched_sentences(text, [_edit("SLA-001", "Bad one.", "Ok.", 8)]) == [
            "Thanks.",
            "Thanks.",
        ]

    def test_offsets_are_followed_into_a_later_stage(self):
        text = "Dental is $50,000. I promise. Bye now."
        fact = _edit("FACT-CHECK", "$50,000", "$2,500", 10)
        policy = _edit("PROMISE-001", "I promise.", "Noted.", 18)  # into the fact-fixed text
        assert untouched_sentences(text, [fact, policy]) == ["Bye now."]


class TestLostSentences:
    def test_each_untouched_sentence_needs_its_own_delivered_copy(self):
        assert lost_sentences(["Thanks.", "Thanks."], "Thanks. Ok.") == ["Thanks."]
        assert lost_sentences(["Thanks.", "Thanks."], "Thanks. Ok. Thanks.") == []

    def test_order_matters(self):
        assert lost_sentences(["First one.", "Second one."], "Second one. First one.") == [
            "Second one."
        ]

    def test_a_changed_sentence_is_lost(self):
        assert lost_sentences(["Have a nice day."], "Fixed. Have a nice day!") == [
            "Have a nice day."
        ]


class TestCitationIndex:
    @pytest.fixture
    async def citations(self):
        return await CitationIndex.load()

    def test_rule_and_fact_citations_resolve(self, citations):
        rule = _edit(
            "CREDIT-001",
            "x",
            "y",
            0,
            source_document="Credit_Policy_2024_v3.2",
            source_section="Section 4.1",
        )
        fact = _edit(
            "FACT-CHECK",
            "$5,000",
            "$2,500",
            0,
            source_document="Policy_Document_Standard_2024.pdf",
            source_section="p. 12",
        )
        both = _edit(
            "SLA-001, PROMISE-001",
            "x",
            "y",
            0,
            source_document="SLA_Policy_2024_v1.0; Agent_Conduct_Policy_2024_v1.0",
            source_section="Section 1.2; Section 2.3",
        )
        assert all(citations.resolves(edit) for edit in (rule, fact, both))

    async def test_two_rules_of_one_document_cite_it_once(self, citations):
        """The rewriter joins sources without repeats: one document, two sections."""
        result = await run_autocorrection(
            "I promise our internal tool will never fail you again.",
            customer_context={"account_tenure_days": 200},
            kg_store=KnowledgeGraphStore(),
        )
        (edit,) = result.edits
        assert edit.rule_id == "PROMISE-001, DISC-001"
        assert ";" not in edit.source_document and ";" in edit.source_section
        assert citations.resolves(edit)

    @pytest.mark.parametrize(
        ("rule_id", "document", "section", "after"),
        [
            ("CREDIT-001", "Credit_Policy_2024_v3.2", "Section 9.9", "y"),
            ("CREDIT-001", "Refund_Policy_2024_v2.1", "Section 4.1", "y"),
            ("CREDIT-001", None, "Section 4.1", "y"),
            ("NOPE-001", "Credit_Policy_2024_v3.2", "Section 4.1", "y"),
            # A source the cited rule does not come from
            (
                "CREDIT-001",
                "Credit_Policy_2024_v3.2; SLA_Policy_2024_v1.0",
                "Section 4.1; Section 1.2",
                "y",
            ),
            ("SLA-001, PROMISE-001", "SLA_Policy_2024_v1.0", "Section 1.2", "y"),
            # Sections paired with the wrong documents
            (
                "SLA-001, PROMISE-001",
                "SLA_Policy_2024_v1.0; Agent_Conduct_Policy_2024_v1.0",
                "Section 2.3; Section 1.2",
                "y",
            ),
            # A source listed twice
            (
                "CREDIT-001",
                "Credit_Policy_2024_v3.2; Credit_Policy_2024_v3.2",
                "Section 4.1",
                "y",
            ),
            ("FACT-CHECK", "Policy_Document_Standard_2024.pdf", "p. 99", "$2,500"),
            ("FACT-CHECK", "Policy_Document_Standard_2024.pdf", "Section 4.1", "$2,500"),
            # Page 8 exists, but it is the emergency-room page ($10,000), not dental
            ("FACT-CHECK", "Policy_Document_Standard_2024.pdf", "p. 8", "$2,500"),
            ("FACT-CHECK", "Policy_Document_Premium_2024.pdf", "p. 12", "$2,500"),
        ],
    )
    def test_wrong_or_missing_citation_does_not_resolve(
        self, citations, rule_id, document, section, after
    ):
        edit = _edit(rule_id, "x", after, 0, source_document=document, source_section=section)
        assert not citations.resolves(edit)


class TestOutputHashes:
    RECORD = {"id": "A", "action": "allow", "text": "Hi.", "rules": [], "pii": [], "edits": []}

    def test_digest_changes_with_any_output(self):
        assert outputs_digest([self.RECORD]) == outputs_digest([dict(self.RECORD)])
        assert outputs_digest([self.RECORD]) != outputs_digest([{**self.RECORD, "text": "Hi!"}])

    def test_one_hash_per_case(self):
        other = {**self.RECORD, "id": "B"}
        hashes = record_hashes([self.RECORD, other])
        assert list(hashes) == ["A", "B"]
        assert hashes["A"] != hashes["B"]
        assert record_hashes([{**self.RECORD, "text": "Hi!"}, other])["B"] == hashes["B"]


class TestGoldenInvariants:
    """The published numbers (docs/evaluation.md, "Corrected text")."""

    _cached = None

    @pytest.fixture
    async def report(self):
        # One pipeline pass for the class (about five seconds)
        if TestGoldenInvariants._cached is None:
            TestGoldenInvariants._cached = await run_invariants(GOLDEN_DATASET.cases)
        return TestGoldenInvariants._cached

    @staticmethod
    def _kn(report, check: str) -> tuple[int, int]:
        return report.by_check[check].k, report.by_check[check].n

    def test_every_corrected_case_is_idempotent(self, report):
        assert len(report.cases) == 217
        assert self._kn(report, "idempotent") == (217, 217)

    def test_every_edit_is_cited_and_cases_without_edits_are_not_counted(self, report):
        assert self._kn(report, "cited") == (170, 170)
        pii_only = report.by_tier["pii-only"]
        assert (pii_only["cited"].n, pii_only["replays"].n, pii_only["local"].n) == (0, 0, 0)

    def test_untouched_sentences_are_delivered_unchanged(self, report):
        assert self._kn(report, "local") == (13, 13)

    def test_edit_log_replays_except_for_three_trailing_spaces(self, report):
        assert self._kn(report, "replays") == (167, 170)
        failed = sorted(row.case_id for row in report.cases if row.replays is False)
        assert failed == ["TP-023", "TP-039", "TP-042"]

    def test_tiers(self, report):
        counts = {tier: checks["idempotent"].n for tier, checks in report.by_tier.items()}
        assert counts == {"pii-only": 47, "span": 152, "template": 18}

    def test_outputs_match_the_recorded_hashes(self, report):
        """One hash per case, the same on every Python version and hash seed CI runs."""
        recorded = json.loads(OUTPUTS_FILE.read_text(encoding="utf-8"))
        actual = report.case_hashes
        changed = sorted(i for i in actual.keys() & recorded.keys() if actual[i] != recorded[i])
        added = sorted(actual.keys() - recorded.keys())
        removed = sorted(recorded.keys() - actual.keys())
        assert not (changed or added or removed), (
            f"golden outputs differ from {OUTPUTS_FILE.name}: changed {changed}, "
            f"not recorded {added}, no longer produced {removed}. "
            f"If the change is intended, re-record with: {RECORD_COMMAND}"
        )

    def test_report_prints_counts_without_intervals(self, report):
        text = format_invariants(report, scope="self-authored, in-sample")
        assert "Scope: self-authored, in-sample" in text
        assert "replays     167/170" in text
        assert "0/0" not in text and "%" not in text


async def test_golden_edit_batches():
    """Published: 169 of the 170 edited cases are one batch; none has two attempts."""
    store = KnowledgeGraphStore()
    batch_counts: dict[int, list[str]] = {}
    for case in GOLDEN_DATASET.cases:
        if case.expected_action is not ExpectedAction.CORRECT:
            continue
        result = await run_autocorrection(
            case.agent_text, customer_context=case.customer_context, kg_store=store
        )
        if result.edits:
            batch_counts.setdefault(len(edit_batches(result.edits)), []).append(case.id)
    assert {n: len(ids) for n, ids in batch_counts.items()} == {1: 169, 2: 1}
    assert batch_counts[2] == ["MULTI-001"]


async def test_invariants_report_a_non_idempotent_correction():
    """A check that cannot fail proves nothing: feed it a case built to fail."""
    store = KnowledgeGraphStore()
    case = EvalCase(
        id="X",
        category="true_positive",
        description="",
        agent_text="I promise it is fixed. Have a nice day.",
        customer_context={"account_tenure_days": 200},
        expected_action=ExpectedAction.CORRECT,
    )
    result = await run_autocorrection(case.agent_text, case.customer_context, kg_store=store)
    broken = result.model_copy(
        update={
            "corrected_text": "I guarantee it. Have a nice day!",
            "edits": [
                edit.model_copy(update={"source_section": "Section 0"}) for edit in result.edits
            ],
        }
    )
    row = await check_case(case, broken, store, await CitationIndex.load())
    assert (row.idempotent, row.replays, row.local, row.cited) == (False, False, False, False)


async def test_a_correction_without_edits_has_nothing_to_replay_or_cite():
    store = KnowledgeGraphStore()
    case = EvalCase(
        id="P",
        category="pii_leak",
        description="",
        agent_text="Your SSN is 123-45-6789. Have a nice day.",
        customer_context={"account_tenure_days": 200},
        expected_action=ExpectedAction.CORRECT,
    )
    result = await run_autocorrection(case.agent_text, case.customer_context, kg_store=store)
    assert result.action_taken.value == "correct" and not result.edits
    row = await check_case(case, result, store, await CitationIndex.load())
    assert (row.idempotent, row.replays, row.local, row.cited) == (True, None, None, None)


# --- scripts/evaluate.py --invariants ---


def _evaluate(*argv: str) -> subprocess.CompletedProcess[str]:
    # Keep the empty keys conftest pins: without them the child would read a developer's .env
    env = {
        **{k: v for k, v in os.environ.items() if not k.endswith("API_KEY")},
        "AUTOCORRECTOR_ANTHROPIC_API_KEY": "",
        "AUTOCORRECTOR_OPENAI_API_KEY": "",
    }
    return subprocess.run(
        [sys.executable, "scripts/evaluate.py", *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


class TestEvaluateInvariantsCli:
    def test_child_argv_comes_from_the_parsed_arguments(self):
        args = argparse.Namespace(category=None, case="TP-023", seeds=3, json=False)
        argv = evaluate._child_argv(args)
        assert argv[1:] == [evaluate.__file__, "--invariants", "--json", "--case", "TP-023"]
        assert not any(a.startswith("--seed") for a in argv)
        by_category = argparse.Namespace(category="pii_leak", case=None, seeds=1, json=True)
        assert evaluate._child_argv(by_category)[2:] == [
            "--invariants",
            "--json",
            "--category",
            "pii_leak",
        ]

    def test_json_with_seeds_equals_form_reports_each_seed_and_exits_0(self):
        # --seeds=1 once reached the child unchanged and it respawned without end.
        # TP-023 fails the replay check: a failed check alone does not change the exit code.
        proc = _evaluate("--invariants", "--case", "TP-023", "--json", "--seeds=1")
        assert proc.returncode == 0, proc.stderr[-2000:]
        payload = json.loads(proc.stdout)
        assert payload["scope"].startswith("self-authored, in-sample")
        assert payload["by_check"]["replays"] == {"k": 0, "n": 1}
        assert payload["seeds"] == [
            {"seed": 1, "outputs_sha256": payload["outputs_sha256"], "same": True}
        ]
        assert payload["failures"][0]["case_id"] == "TP-023"

    def test_text_report_names_the_scope(self):
        proc = _evaluate("--invariants", "--case", "TN-001")
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "Scope: self-authored, in-sample" in proc.stdout

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (("--invariants", "--seed", "2"), "unrecognized arguments"),  # abbreviations are off
            (("--invariants", "--dataset", "heldout"), "golden set only"),
            (("--invariants", "--seeds", "-1"), "0 or more"),
            (("--invariants", "--llm-judge"), "no model in the loop"),
            (("--seeds", "2"), "need --invariants"),
            (("--record-outputs",), "need --invariants"),
            (("--invariants", "--record-outputs", "--case", "TP-023"), "whole golden set"),
            (("--category", "pii_leak", "--case", "TP-023"), "give one"),
        ],
    )
    def test_refused_argument_combinations(self, argv, message):
        proc = _evaluate(*argv)
        assert proc.returncode == 2
        assert "usage:" in proc.stderr and message in proc.stderr

    def test_refuses_when_a_key_is_configured(self):
        env = {**os.environ, "AUTOCORRECTOR_ANTHROPIC_API_KEY": "not-a-real-key"}
        proc = subprocess.run(
            [sys.executable, "scripts/evaluate.py", "--invariants", "--case", "TN-001"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 2
        assert "offline path" in proc.stderr

    async def test_record_outputs_writes_the_committed_file(self, monkeypatch, tmp_path):
        target = tmp_path / "golden_outputs.json"
        monkeypatch.setattr(evaluate, "GOLDEN_OUTPUTS", str(target))
        args = argparse.Namespace(category=None, case=None, seeds=0, json=True, record_outputs=True)
        status = await evaluate._run_invariants(GOLDEN_DATASET, "scope", None, args)
        assert status == 0
        assert target.read_bytes() == OUTPUTS_FILE.read_bytes()

    async def test_record_outputs_keeps_the_file_when_a_seed_run_differs(
        self, monkeypatch, tmp_path
    ):
        target = tmp_path / "golden_outputs.json"
        monkeypatch.setattr(evaluate, "GOLDEN_OUTPUTS", str(target))
        monkeypatch.setattr(
            evaluate,
            "_seed_run",
            lambda seed, args, digest: {"seed": seed, "outputs_sha256": "x", "same": False},
        )
        one_case = GOLDEN_DATASET.model_copy(update={"cases": GOLDEN_DATASET.cases[:1]})
        args = argparse.Namespace(category=None, case=None, seeds=1, json=True, record_outputs=True)
        assert await evaluate._run_invariants(one_case, "scope", None, args) == 1
        assert not target.exists()

    @pytest.mark.parametrize(
        ("stdout", "stderr", "expected"),
        [
            ('{"outputs_sha256": "abc"}', "", {"seed": 2, "outputs_sha256": "abc", "same": True}),
            ('{"outputs_sha256": "xyz"}', "", {"seed": 2, "outputs_sha256": "xyz", "same": False}),
            (
                "",
                "Traceback: boom",
                {"seed": 2, "outputs_sha256": None, "same": False, "error": "Traceback: boom"},
            ),
        ],
    )
    def test_seed_run_compares_digests(self, monkeypatch, stdout, stderr, expected):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs, argv=argv)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=stderr)

        monkeypatch.setattr(evaluate.subprocess, "run", fake_run)
        args = argparse.Namespace(category=None, case=None, seeds=2, json=True)
        assert evaluate._seed_run(2, args, "abc") == expected
        assert seen["env"]["PYTHONHASHSEED"] == "2"
        assert seen["timeout"] > 0

    def test_seed_run_timeout_is_a_difference(self, monkeypatch):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        monkeypatch.setattr(evaluate.subprocess, "run", fake_run)
        args = argparse.Namespace(category=None, case=None, seeds=1, json=True)
        result = evaluate._seed_run(1, args, "abc")
        assert result["same"] is False and "timed out" in result["error"]

    def test_exit_status_is_1_only_when_a_seed_differs(self):
        same = {"seed": 1, "outputs_sha256": "abc", "same": True}
        assert evaluate._exit_status([]) == 0
        assert evaluate._exit_status([same]) == 0
        assert evaluate._exit_status([same, {**same, "seed": 2, "same": False}]) == 1
