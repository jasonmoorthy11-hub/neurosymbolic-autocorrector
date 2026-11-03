"""Tests for reading Messages API responses that may start with a thinking block."""

from unittest.mock import MagicMock

import pytest
from anthropic.types import Message, TextBlock, ThinkingBlock, Usage
from anthropic.types.beta import BetaMessage

from autocorrector.llm_response import answer_text, completed_text, is_complete


def _message(*blocks) -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-5-5",
        content=list(blocks),
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _fallback_block() -> dict:
    return {
        "type": "fallback",
        "from": {"model": "claude-opus-5-5"},
        "to": {"model": "claude-opus-5"},
        "trigger": {"type": "refusal"},
    }


def _beta_message(*blocks: dict, stop_reason: str = "end_turn") -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": list(blocks),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def test_answer_text_skips_leading_thinking_block():
    msg = _message(
        ThinkingBlock(type="thinking", thinking="", signature="sig"),
        TextBlock(type="text", text="  hello  "),
    )
    assert answer_text(msg) == "  hello  "


def test_answer_text_plain_text_block():
    msg = _message(TextBlock(type="text", text="only text"))
    assert answer_text(msg) == "only text"


def test_answer_text_no_text_block_returns_empty():
    msg = _message(ThinkingBlock(type="thinking", thinking="", signature="sig"))
    assert answer_text(msg) == ""


def test_answer_text_joins_split_text_blocks():
    msg = _message(
        TextBlock(type="text", text="Your plan covers "),
        TextBlock(type="text", text="$2,500."),
    )
    assert answer_text(msg) == "Your plan covers $2,500."


def test_answer_text_accepts_legacy_mock_shape():
    response = MagicMock()
    response.content = [MagicMock(text="mocked")]
    assert answer_text(response) == "mocked"


def test_answer_text_uses_fallback_model_output_after_seam():
    msg = _beta_message(_fallback_block(), {"type": "text", "text": "Served by fallback."})
    assert answer_text(msg) == "Served by fallback."


def test_answer_text_discards_partial_output_before_seam():
    """Text streamed by the model that refused stays in content; it must never ship."""
    msg = _beta_message(
        {"type": "text", "text": "PARTIAL FROM REFUSED MODEL"},
        _fallback_block(),
        {"type": "text", "text": "continuation"},
    )
    assert answer_text(msg) == ""
    assert completed_text(msg) == ""


def test_is_complete_and_completed_text():
    ok = _message(TextBlock(type="text", text="done"))
    assert is_complete(ok)
    assert completed_text(ok) == "done"

    cut = _message(TextBlock(type="text", text="partial"))
    cut.stop_reason = "max_tokens"
    assert not is_complete(cut)
    assert completed_text(cut) == ""

    legacy = MagicMock()
    legacy.content = [MagicMock(text="mocked")]
    assert is_complete(legacy)


@pytest.mark.parametrize(
    "reason",
    ["refusal", "pause_turn", "model_context_window_exceeded", "compaction", "tool_use", "new"],
)
def test_completed_text_discards_any_non_final_stop_reason(reason):
    msg = _beta_message({"type": "text", "text": "no"}, stop_reason="end_turn")
    msg.stop_reason = reason  # type: ignore[assignment]
    assert not is_complete(msg)
    assert completed_text(msg) == ""


def test_completed_text_accepts_stop_sequence():
    msg = _beta_message({"type": "text", "text": "done"}, stop_reason="stop_sequence")
    assert completed_text(msg) == "done"
