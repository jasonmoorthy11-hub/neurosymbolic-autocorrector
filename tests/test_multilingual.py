"""Tests for multilingual detection, translation, and pipeline integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autocorrector.config import Settings
from autocorrector.multilingual.detector import detect_language
from autocorrector.multilingual.translator import (
    Translation,
    translate_from_english,
    translate_to_english,
    translate_to_english_checked,
)

# --- Language Detector (fast-langdetect, 176 languages) ---


class TestLanguageDetector:
    def test_detect_english(self):
        assert detect_language("I can offer you a $50 credit on your account") == "en"

    def test_detect_spanish(self):
        assert detect_language("Puedo ofrecerle un crédito de $200 en su cuenta") == "es"

    def test_detect_french(self):
        assert detect_language("Je peux vous offrir un crédit de 200$ sur votre compte") == "fr"

    def test_detect_portuguese(self):
        assert detect_language("Posso oferecer um crédito de $200 para você") == "pt"

    def test_detect_german(self):
        assert detect_language("Ich kann Ihnen einen Kredit von 200 Euro anbieten") == "de"

    def test_detect_italian(self):
        assert detect_language("Posso offrirle un credito di 200 euro") == "it"

    def test_detect_arabic(self):
        assert detect_language("أنا يمكنني تقديم رصيد لك") == "ar"

    def test_detect_chinese(self):
        assert detect_language("我可以为您提供200美元的信用额度") == "zh"

    def test_detect_japanese(self):
        assert detect_language("お客様に200ドルのクレジットを提供できます") == "ja"

    def test_detect_korean(self):
        assert detect_language("고객님께 200달러 크레딧을 제공해 드릴 수 있습니다") == "ko"

    def test_detect_russian(self):
        assert detect_language("Я могу предложить вам кредит в 200 долларов") == "ru"

    def test_detect_dutch(self):
        assert detect_language("Ik kan u een tegoed van 200 euro aanbieden") == "nl"

    def test_empty_string_defaults_english(self):
        assert detect_language("") == "en"

    def test_whitespace_defaults_english(self):
        assert detect_language("   ") == "en"

    def test_ambiguous_defaults_english(self):
        result = detect_language("OK")
        assert isinstance(result, str)


# --- Translator ---


class TestTranslator:
    def _mock_client(self, response_text: str) -> AsyncMock:
        client = AsyncMock()
        msg = MagicMock()
        msg.content = [MagicMock(text=response_text)]
        client.messages.create = AsyncMock(return_value=msg)
        return client

    async def test_translate_to_english(self):
        client = self._mock_client("I can offer you a $200 credit")
        result = await translate_to_english("Puedo ofrecerle un crédito de $200", "es", client)
        assert result == "I can offer you a $200 credit"
        client.messages.create.assert_called_once()

    async def test_translate_to_english_noop_for_english(self):
        client = self._mock_client("whatever")
        result = await translate_to_english("Hello world", "en", client)
        assert result == "Hello world"
        client.messages.create.assert_not_called()

    async def test_translate_from_english(self):
        client = self._mock_client("Puedo ofrecerle un crédito de $200")
        result = await translate_from_english("I can offer you a $200 credit", "es", client)
        assert result == "Puedo ofrecerle un crédito de $200"
        client.messages.create.assert_called_once()

    async def test_translate_from_english_noop_for_english(self):
        client = self._mock_client("whatever")
        result = await translate_from_english("Hello world", "en", client)
        assert result == "Hello world"
        client.messages.create.assert_not_called()

    async def test_translate_to_english_fail_open(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await translate_to_english("Hola mundo", "es", client)
        assert result == "Hola mundo"

    async def test_translate_from_english_fail_open(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await translate_from_english("Hello world", "es", client)
        assert result == "Hello world"

    async def test_translate_uses_lang_name_for_known_lang(self):
        client = self._mock_client("Translated text")
        await translate_to_english("Text", "de", client)
        call_args = client.messages.create.call_args
        assert "German" in call_args.kwargs["system"]
        assert call_args.kwargs["output_config"] == {"effort": "low"}

    async def test_translate_from_english_uses_low_effort(self):
        client = self._mock_client("Hola")
        await translate_from_english("Hello", "es", client)
        assert client.messages.create.call_args.kwargs["output_config"] == {"effort": "low"}

    async def test_translate_uses_code_for_unknown_lang(self):
        client = self._mock_client("Translated text")
        await translate_to_english("Text", "sw", client)
        call_args = client.messages.create.call_args
        assert "sw" in call_args.kwargs["system"]


# --- Pipeline Integration ---


class TestPipelineMultilingual:
    async def test_detect_and_translate_node_english(self):
        from autocorrector.orchestrator.pipeline import detect_and_translate_node

        state = {"agent_text": "I can offer you a $50 credit"}
        result = await detect_and_translate_node(state)
        assert result["detected_language"] == "en"
        assert "agent_text" not in result  # no translation needed

    @patch("autocorrector.orchestrator.pipeline.get_settings")
    async def test_detect_and_translate_node_spanish(self, mock_settings):
        from autocorrector.orchestrator.pipeline import detect_and_translate_node

        settings = MagicMock()
        settings.supported_languages = ["en", "es", "fr", "pt", "de", "ar", "zh"]
        settings.anthropic_api_key = "test-key"
        settings.default_llm = "claude-sonnet-5-5"
        mock_settings.return_value = settings

        state = {"agent_text": "Puedo ofrecerle un crédito de $200 en su cuenta"}

        with (
            patch(
                "anthropic.AsyncAnthropic",
            ),
            patch(
                "autocorrector.multilingual.translator.translate_to_english_checked",
                new_callable=AsyncMock,
                return_value=Translation("I can offer you a $200 credit on your account", ok=True),
            ),
        ):
            result = await detect_and_translate_node(state)

        assert result["detected_language"] == "es"
        assert result["agent_text"] == "I can offer you a $200 credit on your account"
        assert (
            result["original_text_untranslated"]
            == "Puedo ofrecerle un crédito de $200 en su cuenta"
        )

    async def test_translate_output_english_noop(self):
        from autocorrector.orchestrator.pipeline import _translate_output_if_needed

        state = {"detected_language": "en"}
        result = await _translate_output_if_needed("Hello world", state)
        assert result == "Hello world"

    @patch("autocorrector.orchestrator.pipeline.get_settings")
    async def test_translate_output_spanish(self, mock_settings):
        from autocorrector.orchestrator.pipeline import _translate_output_if_needed

        settings = MagicMock()
        settings.supported_languages = ["en", "es", "fr", "pt"]
        settings.anthropic_api_key = "test-key"
        settings.default_llm = "claude-sonnet-5-5"
        mock_settings.return_value = settings

        state = {"detected_language": "es"}

        with (
            patch(
                "anthropic.AsyncAnthropic",
            ),
            patch(
                "autocorrector.multilingual.translator.translate_from_english",
                new_callable=AsyncMock,
                return_value="Puedo ofrecerle un crédito de $50",
            ),
        ):
            result = await _translate_output_if_needed("I can offer you a $50 credit", state)

        assert result == "Puedo ofrecerle un crédito de $50"

    async def test_correction_result_has_detected_language(self):
        from autocorrector.models import (
            CorrectionAction,
            CorrectionResult,
            DetectedIntent,
            IntentType,
            ValidationResult,
        )

        result = CorrectionResult(
            original_text="test",
            corrected_text="test",
            was_modified=False,
            action_taken=CorrectionAction.ALLOW,
            confidence=1.0,
            intent=DetectedIntent(intent_type=IntentType.GENERAL, confidence=1.0),
            validation=ValidationResult(is_compliant=True, action=CorrectionAction.ALLOW),
            detected_language="es",
        )
        assert result.detected_language == "es"

    async def test_correction_result_defaults_to_english(self):
        from autocorrector.models import (
            CorrectionAction,
            CorrectionResult,
            DetectedIntent,
            IntentType,
            ValidationResult,
        )

        result = CorrectionResult(
            original_text="test",
            corrected_text="test",
            was_modified=False,
            action_taken=CorrectionAction.ALLOW,
            confidence=1.0,
            intent=DetectedIntent(intent_type=IntentType.GENERAL, confidence=1.0),
            validation=ValidationResult(is_compliant=True, action=CorrectionAction.ALLOW),
        )
        assert result.detected_language == "en"


class TestTranslatorThinkingFirst:
    def _thinking_only_client(self, stop_reason: str = "end_turn") -> AsyncMock:
        from anthropic.types import Message, ThinkingBlock, Usage

        msg = Message(
            id="msg_t",
            type="message",
            role="assistant",
            model="claude-sonnet-5-5",
            content=[ThinkingBlock(type="thinking", thinking="", signature="sig")],
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=msg)
        return client

    async def test_to_english_thinking_only_returns_original(self):
        client = self._thinking_only_client()
        assert await translate_to_english("Hola mundo", "es", client) == "Hola mundo"

    async def test_from_english_truncated_returns_original(self):
        client = self._thinking_only_client(stop_reason="max_tokens")
        assert await translate_from_english("Hello world", "es", client) == "Hello world"

    def _thinking_then_text_client(self, text: str) -> AsyncMock:
        from anthropic.types import Message, TextBlock, ThinkingBlock, Usage

        msg = Message(
            id="msg_t",
            type="message",
            role="assistant",
            model="claude-sonnet-5-5",
            content=[
                ThinkingBlock(type="thinking", thinking="", signature="sig"),
                TextBlock(type="text", text=text),
            ],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=msg)
        return client

    async def test_to_english_reads_text_after_thinking(self):
        client = self._thinking_then_text_client("Hello world")
        assert await translate_to_english("Hola mundo", "es", client) == "Hello world"

    async def test_from_english_reads_text_after_thinking(self):
        client = self._thinking_then_text_client("Hola mundo")
        assert await translate_from_english("Hello world", "es", client) == "Hola mundo"


# --- Translation guard: non-English text must never be silently allowed ---

SPANISH_CREDIT = "Puedo ofrecerle un crédito de $200 en su cuenta hoy mismo."
NEW_CUSTOMER = {"customer_id": "C-77", "account_tenure_days": 30}


def _settings(api_key: str) -> Settings:
    return Settings(anthropic_api_key=api_key)


class TestTranslationGuard:
    async def test_translate_checked_reports_failure(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await translate_to_english_checked("Hola mundo", "es", client)
        assert result == Translation("Hola mundo", ok=False)

    async def test_translate_checked_reports_success(self):
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [MagicMock(text="Hello world")]
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=response)
        result = await translate_to_english_checked("Hola mundo", "es", client)
        assert result == Translation("Hello world", ok=True)

    async def test_pipeline_spanish_without_key_escalates(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("")),
            patch("anthropic.AsyncAnthropic") as client_cls,
        ):
            result = await run_autocorrection(SPANISH_CREDIT, NEW_CUSTOMER)

        assert result.detected_language == "es"
        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied
        client_cls.assert_not_called()  # no key: no API client, no network call

    async def test_pipeline_spanish_translation_failure_escalates(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("k")),
            patch("anthropic.AsyncAnthropic"),
            patch(
                "autocorrector.multilingual.translator.translate_to_english_checked",
                new_callable=AsyncMock,
                return_value=Translation(SPANISH_CREDIT, ok=False),
            ),
        ):
            result = await run_autocorrection(SPANISH_CREDIT, NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied

    async def test_pipeline_spanish_pii_still_redacted_on_escalation(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("")):
            result = await run_autocorrection(
                "Su número de seguro social es 123-45-6789, gracias por esperar.",
                NEW_CUSTOMER,
            )

        from autocorrector.models import CorrectionAction

        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.pii_detected is True
        assert "123-45-6789" not in result.corrected_text
        assert "[SSN REDACTED]" in result.corrected_text
        assert "pii-detection" in result.guardrails_applied
        assert "translation-unavailable" in result.guardrails_applied

    async def test_pipeline_spanish_with_translator_takes_normal_path(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("k")),
            patch("anthropic.AsyncAnthropic"),
            patch(
                "autocorrector.multilingual.translator.translate_to_english_checked",
                new_callable=AsyncMock,
                return_value=Translation("I can offer you a $200 credit on your account.", ok=True),
            ),
            patch(
                "autocorrector.multilingual.translator.translate_from_english",
                new_callable=AsyncMock,
                side_effect=lambda text, *a, **k: text,
            ),
        ):
            result = await run_autocorrection(SPANISH_CREDIT, NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.CORRECT
        assert "translation-unavailable" not in result.guardrails_applied
        assert any(v.rule_id == "CREDIT-001" for v in result.validation.violations)

    async def test_pipeline_english_unchanged_by_guard(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("")):
            result = await run_autocorrection(
                "I'd be happy to help you with that today.", NEW_CUSTOMER
            )

        assert result.action_taken == CorrectionAction.ALLOW
        assert "translation-unavailable" not in result.guardrails_applied

    @pytest.mark.parametrize("text", ["OK.", "Plan B", "Merci", "Ciao!", "Gracias por esperar."])
    def test_detect_short_text_defaults_to_english(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        "text",
        ["Crédito de $750 aplicado.", "Le ofrezco $500 crédito.", "Reembolso de $500 aprobado."],
    )
    def test_detect_short_text_with_amount_or_accents_is_detected(self, text):
        assert detect_language(text) == "es"

    @pytest.mark.parametrize("text", ["$50 credit applied.", "Refund of $20.", "Call 555-1234."])
    def test_detect_short_english_with_amount_stays_english(self, text):
        assert detect_language(text) == "en"

    async def test_pipeline_short_spanish_credit_escalates(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("")):
            result = await run_autocorrection("Crédito de $750 aplicado.", NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied

    async def test_pipeline_short_english_reply_is_allowed(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        with patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("")):
            result = await run_autocorrection("OK.", NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.ALLOW
        assert result.corrected_text == "OK."
        assert "translation-unavailable" not in result.guardrails_applied

    async def test_pipeline_unsupported_language_with_key_escalates(self):
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        polish = "Mogę zaoferować panu kredyt w wysokości 200 dolarów jeszcze dzisiaj."
        assert detect_language(polish) == "pl"
        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=_settings("k")),
            patch("anthropic.AsyncAnthropic") as client_cls,
        ):
            result = await run_autocorrection(polish, NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied
        client_cls.assert_not_called()

    async def test_pipeline_nemo_block_keeps_translation_label(self):
        from autocorrector.guardrails.nemo_rails import InputRailResult
        from autocorrector.models import CorrectionAction
        from autocorrector.orchestrator.pipeline import run_autocorrection

        settings = _settings("")
        settings.nemo_rails_enabled = True
        blocked = InputRailResult(allowed=False, rails_triggered=["jailbreak"], reason="x")
        with (
            patch("autocorrector.orchestrator.pipeline.get_settings", return_value=settings),
            patch(
                "autocorrector.guardrails.nemo_rails.check_input",
                new_callable=AsyncMock,
                return_value=blocked,
            ),
        ):
            result = await run_autocorrection(SPANISH_CREDIT, NEW_CUSTOMER)

        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied
        assert "jailbreak" in result.guardrails_applied
