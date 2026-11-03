"""Helpers for reading Claude Messages API responses."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Allowlist: any other stop_reason (max_tokens, refusal, pause_turn, tool_use,
# compaction, model_context_window_exceeded, or one added later) means the
# output is not a finished answer and must not be shipped.
FINAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})

# Server-side refusal fallbacks ("default" routing by refusal category).
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_SERVER_SIDE_FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})


def supports_server_side_fallback(model: str) -> bool:
    """True for models that accept ``fallbacks="default"`` on the Claude API.

    Claude Haiku 5.5 has no server-side fallback; other ids use plain ``messages.create``.
    """
    return model in _SERVER_SIDE_FALLBACK_MODELS or model.startswith("claude-fable-")


def answer_text(response: Any) -> str:
    """Return the answer text of a Messages API response.

    Current Claude models run adaptive thinking by default, so ``response.content``
    may begin with thinking blocks that have no ``.text``; those are skipped and
    the remaining text blocks are joined.

    With server-side fallbacks, a ``fallback`` block marks where a model that
    declined hands over to the next one. Text the declining model streamed
    before the seam stays in ``content``; it must never ship, and text after the
    seam may only be its continuation, so "" is returned in that case. When no
    text precedes the seam, the text after the last seam is the answer.
    """
    blocks = list(getattr(response, "content", None) or [])
    seams = [i for i, block in enumerate(blocks) if getattr(block, "type", None) == "fallback"]
    if seams:
        if any(_text_of(block) is not None for block in blocks[: seams[-1]]):
            logger.warning("Fallback response contains output from a declining model; discarding")
            return ""
        blocks = blocks[seams[-1] + 1 :]
    return "".join(text for block in blocks if (text := _text_of(block)) is not None)


def _text_of(block: Any) -> str | None:
    text = getattr(block, "text", None)
    return text if isinstance(text, str) else None


def is_complete(response: Any) -> bool:
    """True only for a finished answer (``end_turn`` / ``stop_sequence``).

    Non-string values count as complete: the SDK always returns a string
    stop_reason on non-streaming responses, so only test doubles reach this branch.
    """
    stop_reason = getattr(response, "stop_reason", None)
    return not isinstance(stop_reason, str) or stop_reason in FINAL_STOP_REASONS


def completed_text(response: Any) -> str:
    """Answer text of a complete response; "" if truncated, refused, paused, or text-less."""
    if not is_complete(response):
        logger.warning(
            "LLM response incomplete (stop_reason=%s); discarding output",
            getattr(response, "stop_reason", None),
        )
        return ""
    return answer_text(response)
