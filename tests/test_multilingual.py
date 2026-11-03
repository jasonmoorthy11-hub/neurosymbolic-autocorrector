"""Tests for multilingual detection, translation, and pipeline integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from autocorrector.multilingual.detector import detect_language
from autocorrector.multilingual.translator import translate_from_english, translate_to_english

# --- Language Detector (fast-langdetect, 176 languages) ---


class TestLanguageDetector:
    def test_detect_english(self):
        assert detect_language("I can offer you a $50 credit on your account") == "en"

    def test_detect_spanish(self):
        assert detect_language("Puedo ofrecerle un crédito de $200 en su cuenta") == "es"

    def test_detect_french(self):
        assert (
            detect_language("Je peux vous offrir un crédit de 200$ sur votre compte")
            == "fr"
        )

    def test_detect_portuguese(self):
        assert detect_language("Posso oferecer um crédito de $200 para você") == "pt"

    def test_detect_german(self):
        assert (
            detect_language("Ich kann Ihnen einen Kredit von 200 Euro anbieten") == "de"
        )

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
        result = await translate_to_english(
            "Puedo ofrecerle un crédito de $200", "es", client
        )
        assert result == "I can offer you a $200 credit"
        client.messages.create.assert_called_once()

    async def test_translate_to_english_noop_for_english(self):
        client = self._mock_client("whatever")
        result = await translate_to_english("Hello world", "en", client)
        assert result == "Hello world"
        client.messages.create.assert_not_called()

    async def test_translate_from_english(self):
        client = self._mock_client("Puedo ofrecerle un crédito de $200")
        result = await translate_from_english(
            "I can offer you a $200 credit", "es", client
        )
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
        settings.default_llm = "claude-sonnet-4-6"
        mock_settings.return_value = settings

        state = {"agent_text": "Puedo ofrecerle un crédito de $200 en su cuenta"}

        with patch(
            "anthropic.AsyncAnthropic",
        ), patch(
            "autocorrector.multilingual.translator.translate_to_english",
            new_callable=AsyncMock,
            return_value="I can offer you a $200 credit on your account",
        ):
            result = await detect_and_translate_node(state)

        assert result["detected_language"] == "es"
        assert result["agent_text"] == "I can offer you a $200 credit on your account"
        assert result["original_text_untranslated"] == "Puedo ofrecerle un crédito de $200 en su cuenta"

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
        settings.default_llm = "claude-sonnet-4-6"
        mock_settings.return_value = settings

        state = {"detected_language": "es"}

        with patch(
            "anthropic.AsyncAnthropic",
        ), patch(
            "autocorrector.multilingual.translator.translate_from_english",
            new_callable=AsyncMock,
            return_value="Puedo ofrecerle un crédito de $50",
        ):
            result = await _translate_output_if_needed(
                "I can offer you a $50 credit", state
            )

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
