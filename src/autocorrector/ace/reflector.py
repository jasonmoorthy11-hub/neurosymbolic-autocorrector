"""ACE Reflector — extracts correction strategies from supervisor feedback."""

from __future__ import annotations

import logging
from typing import Any

from autocorrector.llm_response import completed_text

logger = logging.getLogger(__name__)

REFLECTOR_SYSTEM_PROMPT = (
    "You are an expert at analyzing customer service corrections. "
    "Given a rejected correction, extract ONE concise lesson (1-2 sentences) "
    "that would help future corrections avoid the same mistake.\n\n"
    "Focus on WHAT went wrong and HOW to fix it. Be specific and actionable.\n"
    "Output ONLY the lesson text. No prefixes, labels, or explanations."
)


def build_reflector_prompt(
    original_text: str,
    corrected_text: str,
    violations: list[dict[str, Any]],
    rejection_reason: str | None,
    reviewer_comment: str | None,
) -> str:
    """Build the user prompt for the reflector LLM."""
    parts = [
        f"ORIGINAL AGENT TEXT:\n{original_text}\n",
        f"SYSTEM'S CORRECTION:\n{corrected_text}\n",
    ]

    if violations:
        parts.append("VIOLATIONS DETECTED:")
        for v in violations:
            parts.append(f"  - [{v.get('rule_id', '?')}] {v.get('description', '')}")
        parts.append("")

    if rejection_reason:
        parts.append(f"REJECTION REASON: {rejection_reason}")
    if reviewer_comment:
        parts.append(f"REVIEWER COMMENT: {reviewer_comment}")

    parts.append(
        "\nWhat lesson should the correction system learn to avoid this rejection in the future?"
    )
    return "\n".join(parts)


async def extract_lesson(
    anthropic_client: Any,
    model: str,
    original_text: str,
    corrected_text: str,
    violations: list[dict[str, Any]],
    rejection_reason: str | None = None,
    reviewer_comment: str | None = None,
) -> str | None:
    """Use Claude to extract a correction strategy from a rejected event.

    Returns the lesson text, or None on any failure (fail-open).
    """
    if anthropic_client is None:
        logger.debug("No Anthropic client — skipping reflector")
        return None

    if not rejection_reason and not reviewer_comment:
        logger.debug("No rejection reason or comment — skipping reflector")
        return None

    user_prompt = build_reflector_prompt(
        original_text, corrected_text, violations, rejection_reason, reviewer_comment
    )

    try:
        response = await anthropic_client.messages.create(
            model=model,
            max_tokens=4096,
            system=REFLECTOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"effort": "medium"},
        )
        lesson = completed_text(response).strip()
        if not lesson:
            return None
        logger.info("Reflector extracted lesson: %s", lesson[:80])
        return lesson
    except Exception as e:
        logger.warning("Reflector failed: %s", e)
        return None
