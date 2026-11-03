"""Tests for the evaluation framework."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import (
    GOLDEN_DATASET,
    ExpectedAction,
)
from autocorrector.evaluation.heldout import HELDOUT_DATASET
from autocorrector.evaluation.registry import DATASETS, select_dataset
from autocorrector.evaluation.scorer import (
    CaseResult,
    compute_metrics,
    format_report,
    wilson_interval,
)

Z2 = 1.96**2

# --- Wilson interval ---


class TestWilsonInterval:
    """95% Wilson score interval for a binomial proportion."""

    def test_wilson_all_successes(self):
        low, high = wilson_interval(410, 410)
        assert low == pytest.approx(410 / (410 + Z2), abs=1e-12)
        assert high == 1.0

    def test_wilson_no_successes(self):
        low, high = wilson_interval(0, 10)
        assert low == 0.0
        assert high == pytest.approx(Z2 / (10 + Z2), abs=1e-12)

    def test_wilson_half(self):
        low, high = wilson_interval(5, 10)
        assert low == pytest.approx(0.2365896, abs=1e-6)
        assert high == pytest.approx(0.7634104, abs=1e-6)

    def test_wilson_asymmetric(self):
        low, high = wilson_interval(3, 10)
        assert low == pytest.approx(0.107789, abs=1e-6)
        assert high == pytest.approx(0.603227, abs=1e-6)

    @pytest.mark.parametrize("n", range(1, 400))
    def test_wilson_exact_endpoints(self, n):
        # The formula rounds an ulp off 0 or 1 for many n (e.g. 5/5, 0/11, 6/6).
        low, high = wilson_interval(0, n)
        assert low == 0.0
        assert high == pytest.approx(Z2 / (n + Z2), abs=1e-12)
        low, high = wilson_interval(n, n)
        assert high == 1.0
        assert low == pytest.approx(n / (n + Z2), abs=1e-12)

    def test_wilson_rejects_non_positive_z(self):
        with pytest.raises(ValueError, match="z must be positive"):
            wilson_interval(3, 10, z=0.0)
        with pytest.raises(ValueError, match="z must be positive"):
            wilson_interval(3, 10, z=-1.96)

    def test_wilson_empty_sample_is_uninformative(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_wilson_rejects_more_successes_than_trials(self):
        with pytest.raises(ValueError, match="need 0 <= k <= n"):
            wilson_interval(101, 100)

    def test_wilson_rejects_negative_successes(self):
        with pytest.raises(ValueError, match="need 0 <= k <= n"):
            wilson_interval(-1, 10)


# --- Dataset Tests ---


class TestGoldenDataset:
    """Test the golden dataset structure."""

    def test_dataset_has_cases(self):
        assert len(GOLDEN_DATASET.cases) >= 40

    def test_all_cases_have_ids(self):
        ids = [c.id for c in GOLDEN_DATASET.cases]
        assert len(ids) == len(set(ids)), "Duplicate case IDs found"

    def test_categories_present(self):
        categories = set(c.category for c in GOLDEN_DATASET.cases)
        assert "true_positive" in categories
        assert "true_negative" in categories
        assert "escalation" in categories
        assert "factual_error" in categories
        assert "pii_leak" in categories

    def test_balanced_distribution(self):
        """Ensure reasonable distribution across categories."""
        summary = GOLDEN_DATASET.summary
        # Should have multiple cases per category
        for cat in ["true_positive", "true_negative", "escalation", "pii_leak", "factual_error"]:
            assert summary.get(cat, 0) >= 3, f"Category {cat} has too few cases"

    def test_true_negatives_expect_allow(self):
        for case in GOLDEN_DATASET.cases:
            if case.category == "true_negative":
                assert case.expected_action == ExpectedAction.ALLOW, (
                    f"Case {case.id}: true_negative should expect ALLOW"
                )

    def test_escalations_expect_escalate(self):
        for case in GOLDEN_DATASET.cases:
            if case.category == "escalation":
                assert case.expected_action == ExpectedAction.ESCALATE, (
                    f"Case {case.id}: escalation should expect ESCALATE"
                )

    def test_pii_cases_have_expected_pii_flag(self):
        for case in GOLDEN_DATASET.cases:
            if case.category == "pii_leak":
                assert case.expected_pii is True, (
                    f"Case {case.id}: pii_leak should have expected_pii=True"
                )


class TestHeldoutDataset:
    """Structure of the blind held-out set (never tuned on; see heldout.py)."""

    def test_heldout_composition(self):
        actions = [c.expected_action for c in HELDOUT_DATASET.cases]
        assert len(HELDOUT_DATASET.cases) == 60
        assert actions.count(ExpectedAction.ALLOW) == 25  # 20 allow + 5 tricky negatives
        assert actions.count(ExpectedAction.CORRECT) == 25
        assert actions.count(ExpectedAction.ESCALATE) == 10
        assert HELDOUT_DATASET.summary["tricky_negative"] == 5

    def test_heldout_ids_unique_and_disjoint_from_golden(self):
        ids = [c.id for c in HELDOUT_DATASET.cases]
        assert len(ids) == len(set(ids))
        assert not set(ids) & {c.id for c in GOLDEN_DATASET.cases}

    def test_heldout_texts_not_in_golden(self):
        def norm(text: str) -> str:
            return re.sub(r"[^a-z0-9$]+", " ", text.lower()).strip()

        golden_texts = {norm(c.agent_text) for c in GOLDEN_DATASET.cases}
        overlap = [c.id for c in HELDOUT_DATASET.cases if norm(c.agent_text) in golden_texts]
        assert overlap == []

    def test_heldout_covers_every_rule(self):
        rule_ids = {r for c in HELDOUT_DATASET.cases for r in c.expected_rule_ids}
        assert rule_ids == {
            "CREDIT-001",
            "CREDIT-002",
            "ESC-001",
            "REFUND-001",
            "REFUND-002",
            "PROMISE-001",
            "SLA-001",
            "DISC-001",
        }


class TestDatasetRegistry:
    """--dataset selection; never runs the pipeline."""

    def test_select_dataset_golden_is_in_sample(self):
        dataset, scope = select_dataset("golden")
        assert dataset is GOLDEN_DATASET
        assert "in-sample" in scope

    def test_select_dataset_heldout_is_labelled_held_out(self):
        dataset, scope = select_dataset("heldout")
        assert dataset is HELDOUT_DATASET
        assert "held-out" in scope

    def test_select_dataset_unknown_name(self):
        assert sorted(DATASETS) == ["golden", "heldout"]
        with pytest.raises(KeyError):
            select_dataset("bogus")


# --- Scorer Tests ---


class TestScorer:
    """Test the scoring engine."""

    def _make_result(self, expected: str, actual: str, **kwargs) -> CaseResult:
        defaults = {"case_id": "test", "latency_ms": 10.0}
        return CaseResult(
            category="test",
            expected_action=ExpectedAction(expected),
            actual_action=actual,
            is_correct=(expected == actual),
            **{**defaults, **kwargs},
        )

    def test_perfect_score(self):
        results = [
            self._make_result("allow", "allow"),
            self._make_result("correct", "correct"),
            self._make_result("escalate", "escalate"),
        ]
        report = compute_metrics(results)
        assert report.accuracy == 1.0
        assert report.precision == 1.0
        assert report.recall == 1.0
        assert report.f05 == 1.0

    def test_all_wrong(self):
        results = [
            self._make_result("allow", "correct"),
            self._make_result("correct", "allow"),
            self._make_result("escalate", "allow"),
        ]
        # Override is_correct since our helper sets it
        for r in results:
            r.is_correct = False
        report = compute_metrics(results)
        assert report.accuracy == 0.0

    def test_false_positive_hurts_precision(self):
        """Flagging compliant output as violation = false positive."""
        results = [
            self._make_result("allow", "correct"),  # FP
            self._make_result("correct", "correct"),  # TP
        ]
        results[0].is_correct = False
        report = compute_metrics(results)
        assert report.precision == 0.5  # 1 TP / (1 TP + 1 FP)
        assert report.recall == 1.0  # 1 TP / (1 TP + 0 FN)

    def test_false_negative_hurts_recall(self):
        """Missing a real violation = false negative."""
        results = [
            self._make_result("correct", "allow"),  # FN
            self._make_result("correct", "correct"),  # TP
        ]
        results[0].is_correct = False
        report = compute_metrics(results)
        assert report.precision == 1.0  # 1 TP / (1 TP + 0 FP)
        assert report.recall == 0.5  # 1 TP / (1 TP + 1 FN)

    def test_f05_favors_precision(self):
        """F0.5 should penalize false positives more than false negatives."""
        # High precision, low recall
        results_hp = [
            self._make_result("correct", "correct"),  # TP
            self._make_result("correct", "allow"),  # FN
            self._make_result("correct", "allow"),  # FN
        ]
        results_hp[1].is_correct = False
        results_hp[2].is_correct = False

        # Low precision, high recall
        results_hr = [
            self._make_result("correct", "correct"),  # TP
            self._make_result("allow", "correct"),  # FP
            self._make_result("allow", "correct"),  # FP
        ]
        results_hr[1].is_correct = False
        results_hr[2].is_correct = False

        report_hp = compute_metrics(results_hp)
        report_hr = compute_metrics(results_hr)

        # F0.5 should favor the high-precision scenario
        assert report_hp.f05 > report_hr.f05

    def test_latency_stats(self):
        results = [
            self._make_result("allow", "allow", latency_ms=10.0),
            self._make_result("allow", "allow", latency_ms=20.0),
            self._make_result("allow", "allow", latency_ms=30.0),
            self._make_result("allow", "allow", latency_ms=100.0),
        ]
        report = compute_metrics(results)
        assert report.latency.count == 4
        assert report.latency.min_ms == 10.0
        assert report.latency.max_ms == 100.0
        assert report.latency.p50_ms == 30.0  # index 4//2=2 → [10,20,30,100][2]

    def test_confusion_matrix(self):
        results = [
            self._make_result("allow", "allow"),
            self._make_result("allow", "correct"),
            self._make_result("correct", "correct"),
            self._make_result("escalate", "escalate"),
        ]
        results[1].is_correct = False
        report = compute_metrics(results)
        assert report.confusion_matrix["allow"]["allow"] == 1
        assert report.confusion_matrix["allow"]["correct"] == 1
        assert report.confusion_matrix["correct"]["correct"] == 1
        assert report.confusion_matrix["escalate"]["escalate"] == 1

    def test_pii_metrics(self):
        results = [
            CaseResult(
                case_id="1",
                category="pii",
                expected_action=ExpectedAction.CORRECT,
                actual_action="correct",
                is_correct=True,
                expected_pii=True,
                actual_pii=True,
                pii_correct=True,
            ),
            CaseResult(
                case_id="2",
                category="pii",
                expected_action=ExpectedAction.CORRECT,
                actual_action="correct",
                is_correct=True,
                expected_pii=True,
                actual_pii=False,
                pii_correct=False,
            ),
            CaseResult(
                case_id="3",
                category="clean",
                expected_action=ExpectedAction.ALLOW,
                actual_action="allow",
                is_correct=True,
                expected_pii=False,
                actual_pii=False,
                pii_correct=True,
            ),
        ]
        report = compute_metrics(results)
        assert report.pii_recall == 0.5  # 1 TP / (1 TP + 1 FN)
        assert report.pii_precision == 1.0  # 1 TP / (1 TP + 0 FP)

    def test_format_report(self):
        results = [self._make_result("allow", "allow")]
        report = compute_metrics(results, dataset_name="test", dataset_version="1.0")
        text = format_report(report)
        assert "EVALUATION REPORT" in text
        assert "Accuracy" in text
        assert "F0.5" in text

    def test_intervals_core_counts(self):
        results = [
            self._make_result("allow", "correct"),  # FP
            self._make_result("correct", "correct"),  # TP
            self._make_result("correct", "allow"),  # FN
            self._make_result("allow", "allow"),  # TN
        ]
        report = compute_metrics(results)
        iv = report.intervals
        assert (iv["accuracy"].k, iv["accuracy"].n) == (2, 4)
        assert (iv["precision"].k, iv["precision"].n) == (1, 2)
        assert (iv["recall"].k, iv["recall"].n) == (1, 2)
        assert (iv["accuracy"].ci_low, iv["accuracy"].ci_high) == wilson_interval(2, 4)

    def test_intervals_pii_counts(self):
        results = [
            self._make_result("correct", "correct", expected_pii=True, actual_pii=True),
            self._make_result("correct", "correct", expected_pii=True, actual_pii=False),
            self._make_result("allow", "correct", expected_pii=False, actual_pii=True),
        ]
        iv = compute_metrics(results).intervals
        assert (iv["pii_precision"].k, iv["pii_precision"].n) == (1, 2)
        assert (iv["pii_recall"].k, iv["pii_recall"].n) == (1, 2)

    def test_intervals_secondary_checks(self):
        results = [
            self._make_result("correct", "correct", expected_rule_ids=["A"], rules_correct=True),
            self._make_result("correct", "correct", expected_rule_ids=["B"], rules_correct=False),
            self._make_result("allow", "allow", facts_correct=False, pii_correct=False),
        ]
        iv = compute_metrics(results).intervals
        assert (iv["rule_ids"].k, iv["rule_ids"].n) == (1, 2)  # only cases expecting rules
        assert (iv["facts_flag"].k, iv["facts_flag"].n) == (2, 3)
        assert (iv["pii_flag"].k, iv["pii_flag"].n) == (2, 3)

    def test_format_report_shows_intervals_and_secondary_misses(self):
        results = [
            self._make_result("correct", "correct"),
            CaseResult(
                case_id="RULE-MISS",
                category="test",
                expected_action=ExpectedAction.CORRECT,
                actual_action="correct",
                is_correct=True,
                expected_rule_ids=["X"],
                rules_correct=False,
            ),
        ]
        text = format_report(compute_metrics(results))
        assert "Precision:    100.0%  (2/2, 95% CI 34.2-100.0)" in text
        assert "Expected rules found: 0.0%  (0/1, 95% CI 0.0-79.3)" in text
        assert "RULE-MISS: rules expected=['X'], actual=[]" in text

    def test_format_report_lists_pii_and_fact_mismatches(self):
        results = [
            self._make_result(
                "correct", "correct", case_id="PII-MISS", expected_pii=True, pii_correct=False
            ),
            self._make_result(
                "correct",
                "correct",
                case_id="FACT-MISS",
                expected_facts_wrong=True,
                facts_correct=False,
            ),
        ]
        text = format_report(compute_metrics(results))
        assert "PII-MISS: PII expected=True, actual=False" in text
        assert "FACT-MISS: fact error expected=True, actual=False" in text
        assert "PII flag agrees:      50.0%  (1/2, 95% CI" in text


def test_evaluate_json_includes_scope():
    repo = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    proc = subprocess.run(
        [sys.executable, "scripts/evaluate.py", "--case", "TN-001", "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout[proc.stdout.index("\n{") :])
    assert payload["scope"].startswith("self-authored, in-sample")
    assert payload["intervals"]["accuracy"] == {
        "k": 1,
        "n": 1,
        "ci_low": 0.20654329147389294,
        "ci_high": 1.0,
    }


# --- Integration: Run golden dataset through pipeline ---


class TestEvaluationPipeline:
    """Run the golden dataset through the actual pipeline."""

    async def test_run_full_evaluation(self, kg_store):
        """Run all golden dataset cases and verify minimum quality thresholds."""
        from autocorrector.evaluation.runner import run_evaluation

        report = await run_evaluation(GOLDEN_DATASET, kg_store=kg_store)

        # Minimum quality thresholds
        assert report.accuracy >= 0.75, f"Accuracy {report.accuracy:.1%} below 75% threshold"
        assert report.precision >= 0.75, f"Precision {report.precision:.1%} below 75% threshold"
        assert report.recall >= 0.70, f"Recall {report.recall:.1%} below 70% threshold"
        assert report.f05 >= 0.70, f"F0.5 {report.f05:.3f} below 0.70 threshold"

        # PII detection should be high
        assert report.pii_recall >= 0.80, f"PII recall {report.pii_recall:.1%} below 80%"

        # Latency should be fast (in-memory store)
        assert report.latency.p50_ms < 500, f"p50 latency {report.latency.p50_ms:.0f}ms too high"

    async def test_no_true_negative_flagged_as_escalation(self, kg_store):
        """True negatives should never be escalated — that would be catastrophic."""
        from autocorrector.evaluation.runner import evaluate_case

        tn_cases = [c for c in GOLDEN_DATASET.cases if c.category == "true_negative"]
        for case in tn_cases:
            result = await evaluate_case(case, kg_store)
            assert result.actual_action != "escalate", (
                f"Case {case.id} ({case.description}) was incorrectly escalated"
            )
