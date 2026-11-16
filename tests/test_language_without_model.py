"""Language detection when the fastText model cannot be loaded.

No model and no network (a first run offline) used to mean every reply was read
as English and checked against the English rules. Now a reply is English only
when its own words show it; anything else is undetermined and is escalated.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from autocorrector.models import CorrectionAction
from autocorrector.multilingual import detector
from autocorrector.multilingual.detector import UNDETERMINED, detect_language


def _no_model(text: str):
    raise RuntimeError("model download failed")


@pytest.fixture(params=["raises", "not installed"])
def model_unavailable(request, monkeypatch):
    monkeypatch.setattr(detector, "_warned_model_unavailable", False)
    if request.param == "raises":
        monkeypatch.setattr(detector, "_HAS_FAST_LANGDETECT", True)
        monkeypatch.setattr(detector, "_ft_detect", _no_model, raising=False)
    else:
        monkeypatch.setattr(detector, "_HAS_FAST_LANGDETECT", False)


@pytest.mark.usefixtures("model_unavailable")
class TestLanguageWithoutModel:
    @pytest.mark.parametrize(
        "text",
        [
            "Thanks for waiting, your order has shipped and you can track it today.",
            "I can offer you a $75 credit for the trouble with your delivery.",
            "What else can I help you with this afternoon?",
            "OK.",
            "Plan B",
            "12345",
            "",
        ],
    )
    def test_language_without_model_english_that_shows_it_stays_english(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        "text",
        [
            "Le remboursement de votre commande sera effectué demain matin.",
            "Su pedido ya esta en camino y llegara manana por la tarde.",
            "Wir haben Ihre Bestellung heute Morgen verschickt.",
            "ご注文の商品は明日の午前中に発送されます。",
            "Crédito aplicado.",
            # English too: fewer than two marker words, or markers under a fifth of the words
            "Order shipped; tracking emailed shortly.",
            "Your package is out for delivery.",
            "Your previous refund has already been completed.",  # golden ADV-013
            "I'll go ahead and issue you a $75 credit right now.",
        ],
    )
    def test_language_without_model_anything_else_is_undetermined(self, text):
        assert detect_language(text) == UNDETERMINED

    def test_language_without_model_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger=detector.__name__):
            for _ in range(3):
                detect_language("Wir haben Ihre Bestellung heute Morgen verschickt.")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "language model" in warnings[0].getMessage()

    async def test_language_without_model_pipeline_escalates_what_it_cannot_read(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "Puedo ofrecerle un credito de 300 dolares por las molestias.",
            {"customer_id": "C-9001", "account_tenure_days": 20},
        )
        assert result.detected_language == UNDETERMINED
        assert result.action_taken == CorrectionAction.ESCALATE
        assert "translation-unavailable" in result.guardrails_applied

    async def test_language_without_model_pipeline_still_corrects_english(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "I can offer you a $75 credit for the trouble with your delivery.",
            {"customer_id": "C-9002", "account_tenure_days": 20},
        )
        assert result.detected_language == "en"
        assert result.action_taken == CorrectionAction.CORRECT


@pytest.mark.parametrize("result", [[], None, {"lang": "fr"}, {"lang": "fr", "score": "x"}])
def test_language_unusable_result_is_treated_as_no_model(monkeypatch, result):
    monkeypatch.setattr(detector, "_warned_model_unavailable", False)
    monkeypatch.setattr(detector, "_HAS_FAST_LANGDETECT", True)
    monkeypatch.setattr(detector, "_ft_detect", lambda text: result, raising=False)
    assert detect_language("Wir haben Ihre Bestellung heute Morgen verschickt.") == UNDETERMINED
    english = "Thanks for waiting, your order has shipped and you can track it today."
    assert detect_language(english) == "en"


def test_language_without_model_share_of_the_golden_set_that_escalates(monkeypatch):
    """The stated cost: how much ordinary English is held while the model is missing."""
    from autocorrector.evaluation.dataset import GOLDEN_DATASET

    monkeypatch.setattr(detector, "_warned_model_unavailable", True)
    monkeypatch.setattr(detector, "_HAS_FAST_LANGDETECT", False)
    held = [c for c in GOLDEN_DATASET.cases if detect_language(c.agent_text) == UNDETERMINED]
    assert (len(held), len(GOLDEN_DATASET.cases)) == (193, 517)  # README and CHANGELOG say so


def test_language_with_model_is_unchanged():
    with patch.object(detector, "_ft_detect", return_value={"lang": "fr", "score": 0.99}):
        assert detect_language("Le remboursement sera effectué demain matin.") == "fr"
