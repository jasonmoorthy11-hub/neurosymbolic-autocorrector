"""LLM-as-judge evaluation for correction quality scoring.

Uses the Anthropic API to evaluate the quality of autocorrections across
multiple dimensions: fluency, accuracy, completeness, and tone preservation.

This provides a more nuanced assessment than binary pass/fail — useful for
comparing different correction strategies and identifying subtle quality issues.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JudgeScore:
    """Quality scores for a single correction."""

    case_id: str
    fluency: float = 0.0  # 1-5: grammatical correctness, natural flow
    accuracy: float = 0.0  # 1-5: policy compliance of correction
    completeness: float = 0.0  # 1-5: all violations addressed
    tone_preservation: float = 0.0  # 1-5: maintains original helpful tone
    overall: float = 0.0  # weighted average
    rationale: str = ""
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and self.overall > 0


@dataclass
class JudgeReport:
    """Aggregated results from LLM judge evaluation."""

    total_cases: int = 0
    scored_cases: int = 0
    errors: int = 0
    avg_fluency: float = 0.0
    avg_accuracy: float = 0.0
    avg_completeness: float = 0.0
    avg_tone_preservation: float = 0.0
    avg_overall: float = 0.0
    scores: list[JudgeScore] = field(default_factory=list)
    low_quality: list[JudgeScore] = field(default_factory=list)  # overall < 3.0


def _get_client(api_key: str) -> Any:
    """Create Anthropic client. Returns None if unavailable."""
    if not api_key:
        return None
    try:
        import anthropic

        return anthropic.AsyncAnthropic(api_key=api_key)
    except Exception as e:
        logger.warning(f"Failed to create Anthropic client: {e}")
        return None


def build_judge_system_prompt() -> str:
    """System prompt for the LLM judge."""
    return (
        "You are an expert evaluator for a customer service AI autocorrection system. "
        "Your job is to assess the quality of corrected agent responses.\n\n"
        "Score each correction on four dimensions (1-5 scale):\n"
        "1. **Fluency** (1-5): Is the corrected text grammatically correct and natural-sounding?\n"
        "2. **Accuracy** (1-5): Does the correction properly address all policy violations?\n"
        "3. **Completeness** (1-5): Were all violations fixed? Nothing missed?\n"
        "4. **Tone Preservation** (1-5): Does the correction maintain the original helpful, "
        "empathetic customer service tone?\n\n"
        "Respond with ONLY valid JSON in this exact format:\n"
        '{"fluency": N, "accuracy": N, "completeness": N, "tone_preservation": N, '
        '"rationale": "Brief explanation"}\n\n'
        "Where N is an integer from 1 to 5."
    )


def build_judge_user_prompt(
    original_text: str,
    corrected_text: str,
    violations: list[dict[str, Any]],
    action_taken: str,
) -> str:
    """Build the evaluation prompt for a single case."""
    parts = [
        f"ORIGINAL AGENT TEXT:\n{original_text}\n",
        f"CORRECTED TEXT:\n{corrected_text}\n",
        f"ACTION TAKEN: {action_taken}\n",
    ]

    if violations:
        parts.append("VIOLATIONS DETECTED:")
        for i, v in enumerate(violations, 1):
            rule_id = v.get("rule_id", "UNKNOWN")
            desc = v.get("description", "")
            severity = v.get("severity", "medium")
            parts.append(f"  {i}. [{rule_id}] ({severity}) {desc}")
        parts.append("")

    parts.append("Score the quality of this correction.")
    return "\n".join(parts)


def _parse_judge_response(text: str) -> dict[str, Any]:
    """Parse JSON response from the judge LLM."""
    text = text.strip()
    # Handle markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            raise

    return {
        "fluency": max(1, min(5, int(data.get("fluency", 3)))),
        "accuracy": max(1, min(5, int(data.get("accuracy", 3)))),
        "completeness": max(1, min(5, int(data.get("completeness", 3)))),
        "tone_preservation": max(1, min(5, int(data.get("tone_preservation", 3)))),
        "rationale": str(data.get("rationale", "")),
    }


async def judge_correction(
    case_id: str,
    original_text: str,
    corrected_text: str,
    violations: list[dict[str, Any]],
    action_taken: str,
    client: Any,
    model: str = "claude-sonnet-4-6",
) -> JudgeScore:
    """Score a single correction using the LLM judge."""
    if client is None:
        return JudgeScore(case_id=case_id, error="No API client available")

    try:
        system_prompt = build_judge_system_prompt()
        user_prompt = build_judge_user_prompt(
            original_text, corrected_text, violations, action_taken
        )

        response = await client.messages.create(
            model=model,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        result = _parse_judge_response(response.content[0].text)

        # Weighted average: accuracy and completeness weighted higher
        overall = (
            result["fluency"] * 0.2
            + result["accuracy"] * 0.3
            + result["completeness"] * 0.3
            + result["tone_preservation"] * 0.2
        )

        return JudgeScore(
            case_id=case_id,
            fluency=result["fluency"],
            accuracy=result["accuracy"],
            completeness=result["completeness"],
            tone_preservation=result["tone_preservation"],
            overall=round(overall, 2),
            rationale=result["rationale"],
        )

    except Exception as e:
        logger.warning(f"Judge scoring failed for {case_id}: {e}")
        return JudgeScore(case_id=case_id, error=str(e))


async def run_judge_evaluation(
    cases: list[dict[str, Any]],
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> JudgeReport:
    """Run LLM judge evaluation on a list of correction results.

    Each case dict should have: case_id, original_text, corrected_text,
    violations (list of dicts), action_taken.
    """
    client = _get_client(api_key)
    if client is None:
        return JudgeReport(
            total_cases=len(cases),
            errors=len(cases),
            scores=[JudgeScore(case_id=c["case_id"], error="No API key") for c in cases],
        )

    scores: list[JudgeScore] = []
    for case in cases:
        score = await judge_correction(
            case_id=case["case_id"],
            original_text=case["original_text"],
            corrected_text=case["corrected_text"],
            violations=case.get("violations", []),
            action_taken=case.get("action_taken", "correct"),
            client=client,
            model=model,
        )
        scores.append(score)
        logger.info(
            f"[{case['case_id']}] overall={score.overall:.1f} "
            f"(f={score.fluency} a={score.accuracy} "
            f"c={score.completeness} t={score.tone_preservation})"
        )

    valid = [s for s in scores if s.is_valid]
    errors = len(scores) - len(valid)

    report = JudgeReport(
        total_cases=len(cases),
        scored_cases=len(valid),
        errors=errors,
        scores=scores,
        low_quality=[s for s in valid if s.overall < 3.0],
    )

    if valid:
        report.avg_fluency = round(sum(s.fluency for s in valid) / len(valid), 2)
        report.avg_accuracy = round(sum(s.accuracy for s in valid) / len(valid), 2)
        report.avg_completeness = round(sum(s.completeness for s in valid) / len(valid), 2)
        report.avg_tone_preservation = round(
            sum(s.tone_preservation for s in valid) / len(valid), 2
        )
        report.avg_overall = round(sum(s.overall for s in valid) / len(valid), 2)

    return report


def format_judge_report(report: JudgeReport) -> str:
    """Format judge report as human-readable text."""
    lines = [
        f"{'=' * 50}",
        "  LLM JUDGE EVALUATION REPORT",
        f"{'=' * 50}",
        "",
        f"  Total Cases:    {report.total_cases}",
        f"  Scored:         {report.scored_cases}",
        f"  Errors:         {report.errors}",
        "",
        "  --- Average Scores (1-5) ---",
        f"  Fluency:            {report.avg_fluency:.2f}",
        f"  Accuracy:           {report.avg_accuracy:.2f}",
        f"  Completeness:       {report.avg_completeness:.2f}",
        f"  Tone Preservation:  {report.avg_tone_preservation:.2f}",
        f"  Overall:            {report.avg_overall:.2f}",
    ]

    if report.low_quality:
        lines.append("")
        lines.append(f"  --- Low Quality ({len(report.low_quality)}) ---")
        for s in report.low_quality:
            lines.append(f"  {s.case_id}: overall={s.overall:.1f} | {s.rationale}")

    lines.append("")
    lines.append(f"{'=' * 50}")
    return "\n".join(lines)
