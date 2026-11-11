"""False-positive classes the real-turn audit exposed (docs/evaluation.md).

Two were fixed (language of short English fragments, "credit card" after a word
that marks a noun) and the tests pin the mechanism. For the refund rule and the
PROMISE-001 / SLA-001 idioms a carve-out was written, let real violations
through in review and was removed; those tests pin that the rule fires. No
audited turn is copied here: the strings are written for this project and only
share a shape with what the audit flagged.
"""

import pytest

from autocorrector.knowledge_graph.keywords import mentions_keyword
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.multilingual.detector import detect_language

NEW_ACCOUNT = {"account_tenure_days": 10}
LATE_PURCHASE = {"account_tenure_days": 400, "days_since_purchase": 45}


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
            "20.00",  # no letters at all
        ],
    )
    def test_codes_numbers_and_usernames_are_english(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        "text",
        [
            # each of these is detected as German or Catalan at 0.66-0.99
            "what els can i do",
            "sorry im new here",
            "can i help wit anything els",
            "Is ther anything els i can do",
        ],
    )
    def test_english_with_typos_is_english(self, text):
        assert detect_language(text) == "en"

    @pytest.mark.parametrize(
        "text",
        [
            "ok im looking now",  # no marker word
            "im sorry abot that",  # one: a borrowed "sorry" does not count
            "order C3EDQM ref EVXRVC",  # a line of codes, four letter runs
        ],
    )
    def test_english_the_guards_do_not_reach_is_the_known_miss(self, text):
        assert detect_language(text) != "en"

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
            ("Thank you! Ich garantiere Ihnen die volle Erstattung, sorry", "de"),
            ("Su plan The New You ya esta activo", "es"),
            ("Entrega en 24h", "es"),
            ("Llega en 3-5 dias", "es"),
            ("Descuento 20%", "es"),
            # two marker words, under a fifth of the words
            ("Gracias, su pedido llega hoy, puede ver el estado en the app with su cuenta", "es"),
            ("Je peux vous offrir un remboursement complet", "fr"),
            # function words added after review: votre, tu, ihr, ihre
            ("Ihr Paket mit The New You ist da, you know", "de"),
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

    @pytest.mark.parametrize(
        "text",
        [
            "Gracias por esperar.",  # under four plain-ASCII words, no amount: as before v1.6
            "Reembolso aprobado hoy",
            "J’ai rembourse",  # new in v1.6: the curly apostrophe no longer sends it to the detector
            "Danke – erledigt",
        ],
    )
    def test_short_unaccented_foreign_text_without_an_amount_is_the_known_loss(self, text):
        assert detect_language(text) == "en"

    def test_english_words_without_foreign_function_words_make_mixed_text_english(self):
        # The other known loss: a foreign line carrying English words and fewer than
        # two function words of its own.
        assert detect_language("Garantiert: your new Erstattung today") == "en"

    def test_a_product_name_with_one_foreign_function_word_is_the_known_loss(self):
        # "com" is not on the list: it is also what follows the dot in an address
        assert detect_language("Seu pedido com The New You chegou hoje.") == "en"

    def test_a_name_with_foreign_particles_leaves_english_english(self):
        # "de" and "la" are not on the list: they turned this typo-ridden line Catalan
        assert detect_language("what els can you do for mr de la cruz") == "en"
        assert detect_language("Please call Oscar de la Renta about your order") == "en"

    def test_ascii_text_is_never_a_language_of_another_script(self):
        # Three made-up codes; the detector alone answers Chinese.
        assert detect_language("ZKQ14 B07LUAY GCQ8NKM") == "en"
        assert detect_language("我可以为您提供200美元的信用额度") == "zh"


class TestCreditCardIsNotACredit:
    """ "credit card" after a word that marks a noun is a means of payment, not a credit."""

    @pytest.mark.parametrize(
        "text",
        [
            "your credit card on file",
            "we take all major credit cards",
            "a credit-card payment",
            "any credit or debit card",
            "any credit/debit card",
            "We charged your credit card ending 4417 for $600.",
            "Which credit card would you like to use?",
            "You can pay by credit card",
            "the credit card ending 4417",
            "your credit\u00a0card",  # a hard space
            "your credit\tcard",
        ],
    )
    def test_credit_card_is_not_a_mention_of_credit(self, text):
        assert not mentions_keyword(text, ["credit"])

    @pytest.mark.parametrize(
        "text",
        [
            # no noun marker before it: it may be the verb, so it matches
            "Credit Card ending 4417",
            "Citi credit card ending 4417",  # a brand name is not a marker
            "We accept cash and credit card for orders over $600.",
            "Please charge me credit card fees of $600 only once.",
            "debit and credit cards accepted",
            "We charged $120 to credit card ending 4417.",
            "all credit cards are accepted",
        ],
    )
    def test_an_unmarked_credit_card_matches_and_is_the_known_cost(self, text):
        assert mentions_keyword(text, ["credit"])

    @pytest.mark.parametrize(
        "text",
        [
            "a $75 credit on your credit card statement",
            "I credited your card",
            "credit cardholders get a credit",
            "store credit",
            "I'll credit card ending 4417",  # the verb, not the compound
            "we will credit cards ending 4417 and 5521",
            "I've credited card ending 4417",
            "I'm crediting card ending 4417",
            "Let me credit card ending 4417",
            "I'll gladly credit card ending 4417 with $600.",
            "and credit cards ending 4417 and 5521 with $40 each",
            "Then we simply credit card ending in 4417 with 600 dollars",
            "I'll go ahead and credit card ending 4417 for $75.",
            "I'll just credit card ending 4417 $60.",
            "I can refund the order or credit card ending 4417 with $90",
            "I'd credit card ending 4417",
            # "each", "all" and "her" can stand before the verb, so they mark nothing
            "We will each credit card ending 4417 with $60.",
            "We will all credit card ending 4417 with $60.",
            "I'll have her credit card ending 4417 with $75.",
            "We may credit card ending 4417",
            "Let’s credit card ending 4417",
            "I'll\ncredit card ending 4417",
            "an $80 credit\nCard ending 4417 stays on file",  # another line
        ],
    )
    def test_a_real_credit_next_to_a_card_still_matches(self, text):
        assert mentions_keyword(text, ["credit"])

    @pytest.mark.parametrize(
        "text",
        [
            "The $89 renewal went through on your credit card this morning.",
            "The annual plan is $120. Which credit card would you like to use?",
            "I've sent $72 back to your credit card for the gloves.",
        ],
    )
    async def test_an_amount_charged_to_a_credit_card_is_not_a_credit_offer(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=NEW_ACCOUNT
        )
        assert violations == []

    @pytest.mark.parametrize(
        "text",
        [
            "I've credited $85 to your account for the trouble.",
            "I've added an $80 credit, and it will show on your credit card statement.",
            "We accept all major credit cards. I can also add a $75 credit to your account.",
            "I'll add a 75 credit to your account.",  # a bare amount before "credit"
        ],
    )
    async def test_a_credit_over_the_limit_still_fires(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=NEW_ACCOUNT
        )
        assert [v.rule_id for v in violations] == ["CREDIT-001"]

    @pytest.mark.parametrize(
        "text",
        [
            "I'll credit card ending 4417 with $90.",
            "I'll gladly credit card ending 4417 with $90.",
            "I'll go ahead and credit card ending 4417 for $75.",
            "I'll just credit card ending 4417 $60.",
            "I've credited card ending 4417 with $90.",
            "I'm adding an $80 credit\nCard ending 4417 stays on file.",
            "We will each credit card ending 4417 with $60.",
            "I'll have her credit card ending 4417 with $75.",
        ],
    )
    async def test_crediting_a_card_still_fires(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=NEW_ACCOUNT
        )
        assert [v.rule_id for v in violations] == ["CREDIT-001"]


class TestCreditCardNounReading:
    @pytest.mark.parametrize(
        "text",
        [
            # a credit noun that runs into "card" with no punctuation, after a marker
            "I've added the credit card ending 4417 will show $80 tomorrow.",
            # a credit offered as "a credit or debit card refund"
            "Would you like the $75 as a credit or debit card refund?",
        ],
    )
    async def test_a_credit_read_as_the_card_is_the_known_loss(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=NEW_ACCOUNT
        )
        assert violations == []

    async def test_a_count_of_credit_cards_is_not_an_amount_of_credit(self):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="We replaced 600 credit cards last year.",
            intent_type="general",
            customer_context=NEW_ACCOUNT,
        )
        assert violations == []

    async def test_a_capped_credit_takes_the_article_of_the_new_amount(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection("I've added an $80 credit to your account.", NEW_ACCOUNT)
        assert "a $50 credit" in result.corrected_text
        assert "an $50" not in result.corrected_text


class TestRefundRuleFiresOnTheWord:
    """REFUND-001 has no carve-out: five forms of one were tried and each let late refunds through."""

    @pytest.mark.parametrize(
        "text",
        [
            # what each form of the carve-out excused and a review then found
            "You'd like a refund? Sure thing.",
            "I can't refund the shipping fee, just the $80 for the item itself.",
            "Your refund request has been paid out.",
            "We processed the refund request you sent on Monday.",
            "I can't refund you until tomorrow morning.",
            "Since we can't process an exchange I'll issue a refund instead.",
            "Let me pull up your order to process that refund for you.",
            "I'll check that your refund lands today.",
            "I can refund you the $80 if you'd like?",
            "You'd like your money back? Does Friday work?",
            "You wanted a refund and I've sent it, right?",
            "Where should the refund go?",
            "Would you like your money back?",
            "Your refund has been completed and you'll see it in 3 days.",
            "I can offer you a refund for the headphones.",
            "How should we send your refund, back to your card or as store credit?",
        ],
    )
    async def test_late_refunds_fire_however_they_are_worded(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert [v.rule_id for v in violations] == ["REFUND-001"]

    async def test_made_up_late_refunds_written_to_break_the_carve_out_all_fire(self):
        """104 replies that grant a late refund, written by a reviewer against 943cd22.

        The first form of the carve-out flagged 47 of them and the second 103.
        """
        import json
        from pathlib import Path

        probes = json.loads(
            (Path(__file__).parent / "fixtures" / "refund_grant_probes.json").read_text("utf-8")
        )
        store = KnowledgeGraphStore()
        missed = []
        for text in probes:
            violations = await store.check_policy_compliance(
                text=text, intent_type="general", customer_context=LATE_PURCHASE
            )
            if "REFUND-001" not in [v.rule_id for v in violations]:
                missed.append(text)
        assert len(probes) == 104
        assert missed == []

    @pytest.mark.parametrize(
        "text",
        [
            # the false-positive class the real-turn audit found, still there
            "So you'd like a refund for the headphones?",
            "That refund is still pending, it normally lands within a week.",
            "Premium members get unlimited refunds on every order.",
            "I'm sorry, I can't refund this order.",
            "I'll check the status of your refund.",
        ],
    )
    async def test_mentions_that_grant_nothing_still_fire_and_are_the_known_cost(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert [v.rule_id for v in violations] == ["REFUND-001"]

    async def test_the_rewrite_drops_a_status_sentence_that_would_confirm_the_refund(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "I've issued your refund. Your refund is now complete.", LATE_PURCHASE
        )
        assert "complete" not in result.corrected_text
        assert "refund" not in result.corrected_text.lower()


class TestIdiomsStillFire:
    """PROMISE-001 and SLA-001 have no idiom carve-out: each one tried let commitments through."""

    @pytest.mark.parametrize(
        ("text", "rule_id"),
        [
            # the idioms the audit flagged, rewritten again
            ("Promo codes don't last forever, so use yours this month.", "PROMISE-001"),
            ("Sign up for alerts and you'll never miss a clearance event.", "PROMISE-001"),
            ("We will definitely be able to swap the size before it ships.", "SLA-001"),
            # what blanking them let through
            ("Most promos don't last forever, but yours will.", "PROMISE-001"),
            ("There's no way your discount won't last forever.", "PROMISE-001"),
            (
                "You'll never miss another deal, since I've set your account to always get "
                "the lowest price.",
                "PROMISE-001",
            ),
            ("We will definitely be able to ship it to St. Louis by Friday.", "SLA-001"),
            ("We will definitely be able to call you back before lunch.", "SLA-001"),
            # plain commitments
            ("This discount is yours forever.", "PROMISE-001"),
            ("You'll never pay a late fee with us.", "PROMISE-001"),
            ("The fee will definitely be gone by Friday.", "SLA-001"),
        ],
    )
    async def test_the_keyword_fires_inside_an_idiom_too(self, text, rule_id):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context={"account_tenure_days": 400}
        )
        assert [v.rule_id for v in violations] == [rule_id]
