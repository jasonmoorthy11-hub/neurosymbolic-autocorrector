"""Tests for the evaluation framework."""

from __future__ import annotations

from autocorrector.evaluation.dataset import (
    GOLDEN_DATASET,
    ExpectedAction,
)
from autocorrector.evaluation.scorer import CaseResult, compute_metrics, format_report

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


# --- Scorer Tests ---


class TestScorer:
    """Test the scoring engine."""

    def _make_result(self, expected: str, actual: str, **kwargs) -> CaseResult:
        return CaseResult(
            case_id="test",
            category="test",
            expected_action=ExpectedAction(expected),
            actual_action=actual,
            is_correct=(expected == actual),
            latency_ms=kwargs.get("latency_ms", 10.0),
            **{k: v for k, v in kwargs.items() if k != "latency_ms"},
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
