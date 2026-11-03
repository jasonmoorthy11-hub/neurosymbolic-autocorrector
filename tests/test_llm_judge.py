"""Tests for the LLM-as-judge evaluation module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autocorrector.evaluation.llm_judge import (
    JudgeReport,
    JudgeScore,
    _parse_judge_response,
    build_judge_system_prompt,
    build_judge_user_prompt,
    format_judge_report,
    judge_correction,
    run_judge_evaluation,
)

# --- Prompt Construction Tests ---


class TestJudgePrompts:
    """Test system and user prompt builders."""

    def test_system_prompt_has_scoring_dimensions(self):
        prompt = build_judge_system_prompt()
        assert "Fluency" in prompt
        assert "Accuracy" in prompt
        assert "Completeness" in prompt
        assert "Tone Preservation" in prompt

    def test_system_prompt_requests_json(self):
        prompt = build_judge_system_prompt()
        assert "JSON" in prompt

    def test_system_prompt_has_scale(self):
        prompt = build_judge_system_prompt()
        assert "1-5" in prompt

    def test_user_prompt_includes_original_and_corrected(self):
        prompt = build_judge_user_prompt(
            original_text="I can offer you $1000 credit",
            corrected_text="I can offer you $50 credit",
            violations=[],
            action_taken="correct",
        )
        assert "I can offer you $1000 credit" in prompt
        assert "I can offer you $50 credit" in prompt

    def test_user_prompt_includes_violations(self):
        violations = [
            {"rule_id": "CREDIT-001", "description": "Credit over $50", "severity": "high"},
        ]
        prompt = build_judge_user_prompt(
            original_text="original",
            corrected_text="corrected",
            violations=violations,
            action_taken="correct",
        )
        assert "CREDIT-001" in prompt
        assert "high" in prompt
        assert "Credit over $50" in prompt

    def test_user_prompt_empty_violations(self):
        prompt = build_judge_user_prompt(
            original_text="original",
            corrected_text="corrected",
            violations=[],
            action_taken="allow",
        )
        assert "VIOLATIONS" not in prompt
        assert "allow" in prompt

    def test_user_prompt_includes_action(self):
        prompt = build_judge_user_prompt(
            original_text="original",
            corrected_text="corrected",
            violations=[],
            action_taken="escalate",
        )
        assert "escalate" in prompt


# --- JSON Parsing Tests ---


class TestJudgeResponseParsing:
    """Test LLM response parsing."""

    def test_parse_valid_json(self):
        text = '{"fluency": 5, "accuracy": 4, "completeness": 5, "tone_preservation": 4, "rationale": "Good correction"}'
        result = _parse_judge_response(text)
        assert result["fluency"] == 5
        assert result["accuracy"] == 4
        assert result["completeness"] == 5
        assert result["tone_preservation"] == 4
        assert result["rationale"] == "Good correction"

    def test_parse_json_in_code_block(self):
        text = '```json\n{"fluency": 3, "accuracy": 3, "completeness": 3, "tone_preservation": 3, "rationale": "OK"}\n```'
        result = _parse_judge_response(text)
        assert result["fluency"] == 3

    def test_parse_json_with_surrounding_text(self):
        text = 'Here is my evaluation:\n{"fluency": 4, "accuracy": 5, "completeness": 4, "tone_preservation": 4, "rationale": "Well done"}\nThat is my score.'
        result = _parse_judge_response(text)
        assert result["accuracy"] == 5

    def test_parse_clamps_values(self):
        text = '{"fluency": 10, "accuracy": 0, "completeness": -1, "tone_preservation": 99}'
        result = _parse_judge_response(text)
        assert result["fluency"] == 5  # clamped to max
        assert result["accuracy"] == 1  # clamped to min
        assert result["completeness"] == 1  # clamped to min
        assert result["tone_preservation"] == 5  # clamped to max

    def test_parse_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_judge_response("not json at all")


# --- Scoring Tests ---


class TestJudgeScoring:
    """Test the judge_correction async function."""

    async def test_judge_returns_score(self):
        """Mock the Anthropic client and verify score calculation."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"fluency": 5, "accuracy": 4, "completeness": 4, "tone_preservation": 5, "rationale": "Good"}'
            )
        ]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        score = await judge_correction(
            case_id="TEST-001",
            original_text="original",
            corrected_text="corrected",
            violations=[],
            action_taken="correct",
            client=mock_client,
        )

        assert score.case_id == "TEST-001"
        assert score.fluency == 5
        assert score.accuracy == 4
        assert score.completeness == 4
        assert score.tone_preservation == 5
        # overall = 5*0.2 + 4*0.3 + 4*0.3 + 5*0.2 = 1.0 + 1.2 + 1.2 + 1.0 = 4.4
        assert score.overall == 4.4
        assert score.is_valid
        assert mock_client.messages.create.call_args.kwargs["output_config"] == {"effort": "medium"}

    async def test_judge_no_client_returns_error(self):
        score = await judge_correction(
            case_id="TEST-002",
            original_text="original",
            corrected_text="corrected",
            violations=[],
            action_taken="correct",
            client=None,
        )
        assert score.error == "No API client available"
        assert not score.is_valid

    async def test_judge_api_error_returns_error(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("Rate limited"))

        score = await judge_correction(
            case_id="TEST-003",
            original_text="original",
            corrected_text="corrected",
            violations=[],
            action_taken="correct",
            client=mock_client,
        )
        assert "Rate limited" in score.error
        assert not score.is_valid


# --- Report Tests ---


class TestJudgeReport:
    """Test report aggregation and formatting."""

    async def test_run_judge_no_api_key(self):
        cases = [
            {"case_id": "C1", "original_text": "a", "corrected_text": "b"},
            {"case_id": "C2", "original_text": "c", "corrected_text": "d"},
        ]
        report = await run_judge_evaluation(cases, api_key="")
        assert report.total_cases == 2
        assert report.errors == 2
        assert report.scored_cases == 0

    async def test_run_judge_with_mock_client(self):
        cases = [
            {
                "case_id": "C1",
                "original_text": "a",
                "corrected_text": "b",
                "violations": [],
                "action_taken": "correct",
            },
        ]
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"fluency": 4, "accuracy": 4, "completeness": 4, "tone_preservation": 4, "rationale": "Solid"}'
            )
        ]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("autocorrector.evaluation.llm_judge._get_client", return_value=mock_client):
            report = await run_judge_evaluation(cases, api_key="test-key")

        assert report.scored_cases == 1
        assert report.errors == 0
        assert report.avg_overall == 4.0  # all 4s → 4.0

    def test_format_report_output(self):
        report = JudgeReport(
            total_cases=10,
            scored_cases=8,
            errors=2,
            avg_fluency=4.2,
            avg_accuracy=3.8,
            avg_completeness=4.0,
            avg_tone_preservation=4.5,
            avg_overall=4.1,
            low_quality=[
                JudgeScore(case_id="BAD-1", overall=2.5, rationale="Poor correction"),
            ],
        )
        text = format_judge_report(report)
        assert "LLM JUDGE" in text
        assert "Total Cases:    10" in text
        assert "Errors:         2" in text
        assert "4.20" in text  # fluency
        assert "Low Quality" in text
        assert "BAD-1" in text

    def test_judge_score_is_valid(self):
        valid = JudgeScore(case_id="X", overall=3.5)
        assert valid.is_valid

        invalid_error = JudgeScore(case_id="Y", error="fail")
        assert not invalid_error.is_valid

        invalid_zero = JudgeScore(case_id="Z", overall=0.0)
        assert not invalid_zero.is_valid


class TestJudgeIncompleteResponse:
    async def test_truncated_response_reports_error(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.stop_reason = "max_tokens"
        mock_response.content = [MagicMock(text='{"fluency": 5')]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        score = await judge_correction(
            case_id="c1",
            original_text="a",
            corrected_text="b",
            violations=[],
            action_taken="corrected",
            client=mock_client,
        )
        assert score.error is not None
        assert "max_tokens" in score.error


    async def test_reads_scores_after_thinking_block(self):
        from anthropic.types import Message, TextBlock, ThinkingBlock, Usage

        msg = Message(
            id="msg_t",
            type="message",
            role="assistant",
            model="claude-sonnet-5-5",
            content=[
                ThinkingBlock(type="thinking", thinking="", signature="sig"),
                TextBlock(
                    type="text",
                    text='{"fluency": 4, "accuracy": 4, "completeness": 4, '
                    '"tone_preservation": 4, "rationale": "ok"}',
                ),
            ],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=msg)

        score = await judge_correction(
            case_id="c2",
            original_text="a",
            corrected_text="b",
            violations=[],
            action_taken="corrected",
            client=mock_client,
        )
        assert score.error is None
        assert score.accuracy == 4
