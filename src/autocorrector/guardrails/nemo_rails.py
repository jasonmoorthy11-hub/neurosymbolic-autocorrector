"""NeMo Guardrails input rail wrapper.

Provides input-level filtering for jailbreak detection and topic steering
before the main autocorrection pipeline. Gracefully degrades if NeMo
Guardrails is not installed (fail-open).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_nemo_rails_instance: Any = None


@dataclass
class InputRailResult:
    """Result of an input rail check."""

    allowed: bool
    reason: str = ""
    rails_triggered: list[str] = field(default_factory=list)


def get_nemo_rails(config_path: str = "configs/nemo") -> Any:
    """Get or create the singleton NeMo LLMRails instance.

    Returns None if nemoguardrails is not installed.
    """
    global _nemo_rails_instance
    if _nemo_rails_instance is not None:
        return _nemo_rails_instance

    try:
        from nemoguardrails import LLMRails, RailsConfig

        config = RailsConfig.from_path(config_path)
        _nemo_rails_instance = LLMRails(config)
        logger.info("NeMo Guardrails initialized from %s", config_path)
        return _nemo_rails_instance
    except ImportError:
        logger.info("nemoguardrails not installed — input rails disabled (fail-open)")
        return None
    except Exception as e:
        logger.warning("Failed to initialize NeMo Guardrails: %s", e)
        return None


def reset_nemo_rails() -> None:
    """Reset the singleton (for testing)."""
    global _nemo_rails_instance
    _nemo_rails_instance = None


async def check_input(
    text: str,
    config_path: str = "configs/nemo",
) -> InputRailResult:
    """Check input text against NeMo input rails.

    Returns InputRailResult with allowed=True if text passes, or
    allowed=False with reason if blocked. Fails open on any error
    (returns allowed=True).
    """
    rails = get_nemo_rails(config_path)
    if rails is None:
        return InputRailResult(allowed=True, reason="NeMo not available — fail-open")

    try:
        response = await rails.generate_async(messages=[{"role": "user", "content": text}])

        # NeMo returns the bot response — check if it's a refusal/redirect
        bot_message = response.get("content", "") if isinstance(response, dict) else str(response)

        # Check for refusal markers
        refusal_markers = [
            "I can only assist with customer service",
            "connect you with a supervisor",
            "specialized in customer service",
        ]
        blocked = any(marker in bot_message for marker in refusal_markers)

        if blocked:
            rails_triggered = []
            if "supervisor" in bot_message.lower():
                rails_triggered.append("jailbreak_detection")
            if "specialized" in bot_message.lower():
                rails_triggered.append("topic_steering")
            return InputRailResult(
                allowed=False,
                reason=bot_message,
                rails_triggered=rails_triggered or ["input_rail"],
            )

        return InputRailResult(allowed=True)

    except Exception as e:
        logger.warning("NeMo input check failed: %s — fail-open", e)
        return InputRailResult(allowed=True, reason=f"Error: {e} — fail-open")
