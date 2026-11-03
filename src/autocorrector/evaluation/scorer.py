"""Scoring metrics for autocorrector evaluation.

Implements:
    - Precision, Recall, F0.5 (ERRANT-style, favoring precision)
    - Accuracy (overall correct decisions)
    - Latency statistics (p50, p95, p99)
    - Per-category breakdown
    - Confusion matrix
"""

from __future__ import annotations

import math
import statistics

from pydantic import BaseModel, Field

from autocorrector.evaluation.dataset import ExpectedAction


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (default 95%).

    An empty sample carries no information, so it returns (0.0, 1.0). The bounds
    are exactly 0 at k == 0 and exactly 1 at k == n (the formula's float result
    can land an ulp off).
    """
    if not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if z <= 0:
        raise ValueError(f"z must be positive, got {z}")
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    low = 0.0 if k == 0 else center - half
    high = 1.0 if k == n else center + half
    return (low, high)


class CaseResult(BaseModel):
    """Result of evaluating a single test case."""

    case_id: str
    category: str
    expected_action: ExpectedAction
    actual_action: str
    is_correct: bool
    expected_pii: bool = False
    actual_pii: bool = False
    pii_correct: bool = True
    expected_facts_wrong: bool = False
    actual_facts_wrong: bool = False
    facts_correct: bool = True
    expected_rule_ids: list[str] = Field(default_factory=list)
    actual_rule_ids: list[str] = Field(default_factory=list)
    rules_correct: bool = True
    latency_ms: float = 0.0
    error: str | None = None


class LatencyStats(BaseModel):
    """Latency distribution statistics."""

    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float


class Proportion(BaseModel):
    """k successes out of n with a 95% Wilson interval."""

    k: int
    n: int
    ci_low: float
    ci_high: float

    @classmethod
    def of(cls, k: int, n: int) -> Proportion:
        low, high = wilson_interval(k, n)
        return cls(k=k, n=n, ci_low=low, ci_high=high)


class EvaluationReport(BaseModel):
    """Full evaluation report with metrics and details."""

    dataset_name: str
    dataset_version: str
    total_cases: int
    correct: int
    incorrect: int
    errors: int

    # Core metrics
    accuracy: float
    precision: float
    recall: float
    f05: float

    # PII detection
    pii_precision: float
    pii_recall: float

    # Latency
    latency: LatencyStats

    # Per-category breakdown
    category_accuracy: dict[str, float]

    # Confusion matrix: {expected: {actual: count}}
    confusion_matrix: dict[str, dict[str, int]]

    # Counts and 95% Wilson intervals: accuracy, precision, recall, pii_precision,
    # pii_recall, and the secondary agreement checks pii_flag, facts_flag, rule_ids
    intervals: dict[str, Proportion] = Field(default_factory=dict)

    # Individual results
    results: list[CaseResult]
    failures: list[CaseResult]


def compute_metrics(
    results: list[CaseResult],
    dataset_name: str = "unknown",
    dataset_version: str = "0.0",
) -> EvaluationReport:
    """Compute all evaluation metrics from case results."""
    total = len(results)
    correct = sum(1 for r in results if r.is_correct)
    errors = sum(1 for r in results if r.error)
    incorrect = total - correct - errors

    # --- Precision / Recall / F0.5 ---
    # For autocorrection:
    #   TP = correctly identified violation and corrected/escalated
    #   FP = flagged as violation but was actually compliant (false alarm)
    #   FN = missed a real violation (allowed when should have corrected)
    #   TN = correctly allowed compliant output through

    tp = sum(
        1
        for r in results
        if r.expected_action != ExpectedAction.ALLOW and r.actual_action != "allow" and not r.error
    )
    fp = sum(
        1
        for r in results
        if r.expected_action == ExpectedAction.ALLOW and r.actual_action != "allow" and not r.error
    )
    fn = sum(
        1
        for r in results
        if r.expected_action != ExpectedAction.ALLOW and r.actual_action == "allow" and not r.error
    )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    # F0.5 favors precision (beta=0.5 → precision weighted 4x more than recall)
    beta = 0.5
    if precision + recall > 0:
        f05 = (1 + beta**2) * (precision * recall) / (beta**2 * precision + recall)
    else:
        f05 = 0.0

    # --- PII metrics ---
    pii_tp = sum(1 for r in results if r.expected_pii and r.actual_pii)
    pii_fp = sum(1 for r in results if not r.expected_pii and r.actual_pii)
    pii_fn = sum(1 for r in results if r.expected_pii and not r.actual_pii)

    pii_precision = pii_tp / (pii_tp + pii_fp) if (pii_tp + pii_fp) > 0 else 1.0
    pii_recall = pii_tp / (pii_tp + pii_fn) if (pii_tp + pii_fn) > 0 else 1.0

    # --- Latency ---
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    if latencies:
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        latency = LatencyStats(
            count=n,
            mean_ms=statistics.mean(sorted_lat),
            p50_ms=sorted_lat[n // 2],
            p95_ms=sorted_lat[int(n * 0.95)],
            p99_ms=sorted_lat[int(n * 0.99)],
            min_ms=sorted_lat[0],
            max_ms=sorted_lat[-1],
        )
    else:
        latency = LatencyStats(count=0, mean_ms=0, p50_ms=0, p95_ms=0, p99_ms=0, min_ms=0, max_ms=0)

    # --- Per-category accuracy ---
    category_results: dict[str, list[bool]] = {}
    for r in results:
        category_results.setdefault(r.category, []).append(r.is_correct)
    category_accuracy = {cat: sum(vals) / len(vals) for cat, vals in category_results.items()}

    # --- Confusion matrix ---
    confusion: dict[str, dict[str, int]] = {}
    for r in results:
        expected = r.expected_action.value
        actual = r.actual_action
        confusion.setdefault(expected, {})
        confusion[expected][actual] = confusion[expected].get(actual, 0) + 1

    # --- Counts with 95% Wilson intervals ---
    scored = [r for r in results if not r.error]
    with_rules = [r for r in scored if r.expected_rule_ids]
    intervals = {
        "accuracy": Proportion.of(correct, total),
        "precision": Proportion.of(tp, tp + fp),
        "recall": Proportion.of(tp, tp + fn),
        "pii_precision": Proportion.of(pii_tp, pii_tp + pii_fp),
        "pii_recall": Proportion.of(pii_tp, pii_tp + pii_fn),
        "pii_flag": Proportion.of(sum(r.pii_correct for r in scored), len(scored)),
        "facts_flag": Proportion.of(sum(r.facts_correct for r in scored), len(scored)),
        "rule_ids": Proportion.of(sum(r.rules_correct for r in with_rules), len(with_rules)),
    }

    # --- Failures ---
    failures = [r for r in results if not r.is_correct]

    return EvaluationReport(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        total_cases=total,
        correct=correct,
        incorrect=incorrect,
        errors=errors,
        accuracy=correct / total if total > 0 else 0.0,
        precision=precision,
        recall=recall,
        f05=f05,
        pii_precision=pii_precision,
        pii_recall=pii_recall,
        latency=latency,
        category_accuracy=category_accuracy,
        confusion_matrix=confusion,
        intervals=intervals,
        results=results,
        failures=failures,
    )


def _ci(report: EvaluationReport, key: str) -> str:
    """'  (k/n, 95% CI lo-hi)' for a metric, or '' if the report has no interval."""
    iv = report.intervals.get(key)
    if iv is None:
        return ""
    return f"  ({iv.k}/{iv.n}, 95% CI {iv.ci_low * 100:.1f}-{iv.ci_high * 100:.1f})"


def format_report(report: EvaluationReport) -> str:
    """Format an evaluation report as a human-readable string."""
    lines = [
        f"{'=' * 60}",
        f"  EVALUATION REPORT: {report.dataset_name} v{report.dataset_version}",
        f"{'=' * 60}",
        "",
        f"  Total Cases:  {report.total_cases}",
        f"  Correct:      {report.correct}",
        f"  Incorrect:    {report.incorrect}",
        f"  Errors:       {report.errors}",
        "",
        "  --- Core Metrics ---",
        f"  Accuracy:     {report.accuracy:.1%}{_ci(report, 'accuracy')}",
        f"  Precision:    {report.precision:.1%}{_ci(report, 'precision')}",
        f"  Recall:       {report.recall:.1%}{_ci(report, 'recall')}",
        f"  F0.5:         {report.f05:.3f}",
        "",
        "  --- PII Detection ---",
        f"  PII Precision: {report.pii_precision:.1%}{_ci(report, 'pii_precision')}",
        f"  PII Recall:    {report.pii_recall:.1%}{_ci(report, 'pii_recall')}",
        "",
        "  --- Latency ---",
        f"  p50:  {report.latency.p50_ms:.1f} ms",
        f"  p95:  {report.latency.p95_ms:.1f} ms",
        f"  p99:  {report.latency.p99_ms:.1f} ms",
        f"  mean: {report.latency.mean_ms:.1f} ms",
        "",
        "  --- Per-Category Accuracy ---",
    ]

    for cat, acc in sorted(report.category_accuracy.items()):
        lines.append(f"  {cat:20s}  {acc:.1%}")

    lines.append("")
    lines.append("  --- Confusion Matrix ---")
    lines.append(f"  {'Expected':<12} {'allow':>8} {'correct':>8} {'escalate':>8}")
    for expected in ["allow", "correct", "escalate"]:
        row = report.confusion_matrix.get(expected, {})
        lines.append(
            f"  {expected:<12} {row.get('allow', 0):>8} "
            f"{row.get('correct', 0):>8} {row.get('escalate', 0):>8}"
        )

    if report.failures:
        lines.append("")
        lines.append(f"  --- Failures ({len(report.failures)}) ---")
        for f in report.failures:
            lines.append(
                f"  {f.case_id}: expected={f.expected_action.value}, "
                f"actual={f.actual_action} | {f.error or ''}"
            )

    if report.intervals:
        lines.append("")
        lines.append("  --- Secondary Checks (scored separately from the action) ---")
        for label, key in (
            ("PII flag agrees:     ", "pii_flag"),
            ("Fact flag agrees:    ", "facts_flag"),
            ("Expected rules found:", "rule_ids"),
        ):
            iv = report.intervals[key]
            share = f"{iv.k / iv.n:.1%}" if iv.n else "n/a"
            lines.append(f"  {label} {share}{_ci(report, key)}")
        for r in report.results:
            if r.error:
                continue
            if not r.pii_correct:
                lines.append(f"  {r.case_id}: PII expected={r.expected_pii}, actual={r.actual_pii}")
            if not r.facts_correct:
                lines.append(
                    f"  {r.case_id}: fact error expected={r.expected_facts_wrong}, "
                    f"actual={r.actual_facts_wrong}"
                )
            if not r.rules_correct:
                lines.append(
                    f"  {r.case_id}: rules expected={r.expected_rule_ids}, "
                    f"actual={r.actual_rule_ids}"
                )

    lines.append("")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)
