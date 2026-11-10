"""False-positive classes the real-turn audit exposed (docs/evaluation.md), fixed with new cases.

Each test pins the mechanism behind one class. No audited turn is copied here: the
strings are written for this project and only share a shape with what the audit
flagged. The golden rows RT-001 onwards carry the end-to-end checks.
"""

import pytest

from autocorrector.multilingual.detector import detect_language


class TestLanguageOfEnglishFragments:
    """Codes, usernames and typo-ridden English are not another language."""

    @pytest.mark.parametrize(
        "text",
        [
            "Z7KQ2",  # an order code
            "4821",  # a bare number
            "mlopez7.",  # a username
            "this is mlopez7",
            "thanks, tkim22",
            "c302-77ad",
            "ok 2.75",
            "8hd2kqpz1mw",
            "$750 + $20",
        ],
    )
    def test_codes_numbers_and_usernames_are_english(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        "text",
        [
            # each of these is detected as German or Catalan at 0.66-0.99
            "im sorry abot that",
            "what els can i do",
            "sorry im new here",
            "can i help wit anything els",
            "Is ther anything els i can do",
        ],
    )
    def test_english_with_typos_is_english(self, text):
        assert detect_language(text) == "en"

    def test_typos_without_two_english_marker_words_are_the_known_miss(self):
        assert detect_language("ok im looking now") != "en"

    @pytest.mark.parametrize("text", ["I’m sorry…", "“OK” – done", "that’s what you need — sorry"])
    def test_typographic_punctuation_does_not_make_text_foreign(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        ("text", "lang"),
        [
            ("Hola, gracias por esperar, su pedido esta en camino", "es"),
            ("Le puedo ofrecer un credito de $200 en su cuenta", "es"),
            ("Credito de $750 aplicado.", "es"),
            ("Credito de 750 aplicado.", "es"),
            ("Has recibido un credito de 750", "es"),
            ("Sorry, no hay problema, su pedido llega hoy", "es"),
            ("Sorry, volle Erstattung garantiert heute", "de"),
            ("Ich bin in Not geraten", "de"),
            ("Ich kann Ihnen eine Gutschrift von 200 Euro anbieten", "de"),
            ("Posso oferecer um credito de 200 para voce", "pt"),
            ("Ik kan u een tegoed van 200 euro aanbieden", "nl"),
            ("Sorry, ik help u graag", "nl"),
            ("Rimborso di 200 euro", "it"),
            ("Posso offrirle un credito di 200 euro", "it"),
            ("Sorry, je ne peux pas", "fr"),
            ("Je peux vous offrir un remboursement complet", "fr"),
        ],
    )
    def test_unaccented_foreign_sentences_are_still_detected(self, text, lang):
        assert detect_language(text) == lang

    @pytest.mark.parametrize(
        "text",
        [
            "¿Como esta usted?",  # inverted question mark: not typographic punctuation
            "Cre\u0301dito aplicado",  # the accent as a combining mark (NFD)
            "Credito de ₹750 aplicado",
        ],
    )
    def test_other_non_ascii_characters_still_send_short_text_to_the_detector(self, text):
        assert detect_language(text) == "es"

    @pytest.mark.parametrize("text", ["Gracias por esperar.", "Reembolso aprobado hoy"])
    def test_short_unaccented_foreign_text_without_an_amount_is_the_known_loss(self, text):
        # Under four plain-ASCII words and no amount: read as English, as before v1.6.
        assert detect_language(text) == "en"

    def test_two_english_marker_words_make_mixed_text_english(self):
        # The other known loss: a code-switched line with enough English in it.
        assert detect_language("Sorry, thank you, ik help u graag") == "en"

    def test_ascii_text_is_never_a_language_of_another_script(self, monkeypatch):
        from autocorrector.multilingual import detector

        monkeypatch.setattr(detector, "_ft_detect", lambda text: [{"lang": "zh", "score": 0.99}])
        assert detect_language("ref 7KD2P und MXR41 plus 9QWZB notiert") == "en"
        assert detect_language("我可以为您提供200美元的信用额度") == "zh"
