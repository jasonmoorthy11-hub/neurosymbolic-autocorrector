"""False-positive classes the real-turn audit exposed (docs/evaluation.md), fixed with new cases.

Each test pins the mechanism behind one class. No audited turn is copied here: the
strings are written for this project and only share a shape with what the audit
flagged. The golden rows RT-001 onwards carry the end-to-end checks.
"""

import pytest

from autocorrector.knowledge_graph.keywords import mentions_keyword
from autocorrector.knowledge_graph.rules import grants_refund
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

    def test_ascii_text_is_never_a_language_of_another_script(self):
        # Three made-up codes; the detector alone answers Chinese.
        assert detect_language("ZKQ14 B07LUAY GCQ8NKM") == "en"
        assert detect_language("我可以为您提供200美元的信用额度") == "zh"


class TestCreditCardIsNotACredit:
    """ "credit card" names a means of payment; the credit keyword does not match there."""

    @pytest.mark.parametrize(
        "text",
        [
            "your credit card on file",
            "we take all major credit cards",
            "Credit Card ending 4417",
            "a credit-card payment",
            "any credit or debit card",
            "any credit/debit card",
        ],
    )
    def test_credit_card_is_not_a_mention_of_credit(self, text):
        assert not mentions_keyword(text, ["credit"])

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
            "I've credited card ending 4417 with $90.",
            "I'm adding an $80 credit\nCard ending 4417 stays on file.",
        ],
    )
    async def test_crediting_a_card_still_fires(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=NEW_ACCOUNT
        )
        assert [v.rule_id for v in violations] == ["CREDIT-001"]


class TestRefundWordIsNotTheAct:
    """REFUND-001 fires on a clause that grants a refund, not on every mention of one."""

    @pytest.mark.parametrize(
        "text",
        [
            "So you'd like a refund for the headphones?",
            "You're after a refund on the blender, is that right?",
            "Your refund is still in progress and should reach your bank soon.",
            "I can look up the status of your refund for you.",
            "Premium members get unlimited refunds on every order.",
            "Refunds are only available for orders that are still in our warehouse.",
            "I'm sorry, I can't refund this order.",
            "Could you tell me why you'd like the refund?",
            "I see you requested a refund last Tuesday.",
            "We're not able to accept the return after 30 days.",
            "Is this about our refund policy or about a specific order?",
        ],
    )
    async def test_mentions_that_grant_nothing_are_allowed(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert violations == []

    @pytest.mark.parametrize(
        "text",
        [
            "I can offer you a refund for the headphones.",
            "The blender is refunded now, was there something else you needed?",
            "I've put your refund through and it should reach your bank soon.",
            "I can send your refund right now.",
            "A refund is available for your order, and I've issued it.",
            "Our refund policy is strict, but I'll refund you anyway.",
            "I can refund this order.",
            "How would you like your refund, back to your card or as store credit?",
            "Can I offer you a refund instead?",
            "Your earlier refund is still pending. I've also issued a refund for the second pair.",
            "Would you like me to issue the refund today?",
            "I will process your refund right away.",
            "Your refund is on its way.",
            "You are eligible for a refund, so I have started it.",
        ],
    )
    async def test_grants_still_fire(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert [v.rule_id for v in violations] == ["REFUND-001"]

    def test_a_long_last_clause_is_a_question_of_its_own(self):
        assert grants_refund(
            "The blender is refunded now, was there something else you needed?", ["refund"]
        )
        assert not grants_refund("You want a refund on the blender, correct?", ["refund"])

    def test_trailing_marks_after_the_question_mark_do_not_hide_it(self):
        assert not grants_refund("Do you want the refund on the lamp?>", ["refund"])

    async def test_the_rewrite_keeps_a_status_sentence_next_to_the_grant(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "Your earlier refund is still pending. I've also issued a refund for the second pair.",
            LATE_PURCHASE,
        )
        assert result.corrected_text.startswith("Your earlier refund is still pending. ")
        assert "issued a refund" not in result.corrected_text

    async def test_the_maximum_credit_rule_sees_a_credit_to_a_card(self):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="I'll credit card ending 4417 with $600.",
            intent_type="general",
            customer_context={"account_tenure_days": 400},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-002"]

    async def test_a_count_of_credit_cards_is_not_an_amount(self):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="We accept 100 credit cards. Here is a $10 discount.",
            intent_type="general",
            customer_context=NEW_ACCOUNT,
        )
        assert violations == []

    async def test_a_payment_to_a_card_with_no_credit_keyword_is_the_known_miss(self):
        # Caught before v1.6 only because "credit card" read as a credit.
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="As an apology I'm sending $600 to your credit card.",
            intent_type="general",
            customer_context={"account_tenure_days": 400},
        )
        assert violations == []

    async def test_the_capped_amount_takes_the_right_article(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection("Let me apply an $80 credit for the outage.", NEW_ACCOUNT)
        assert result.corrected_text.startswith("Let me apply a $50 credit for the outage.")


class TestIdiomsThatContainAKeyword:
    """A keyword inside an idiom is not what PROMISE-001 or SLA-001 is about."""

    @pytest.mark.parametrize(
        "text",
        [
            "Promo codes don't last forever, so use yours this month.",
            "That price won't last forever.",
            "The trial is not forever.",
            "Sign up for alerts and you'll never miss a clearance event.",
            "We will definitely be able to swap the size before it ships.",
        ],
    )
    async def test_idioms_are_allowed(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context={"account_tenure_days": 400}
        )
        assert violations == []

    @pytest.mark.parametrize(
        ("text", "rule_id"),
        [
            ("This discount is yours forever.", "PROMISE-001"),
            ("Promo codes don't last forever, but your price is locked in forever.", "PROMISE-001"),
            ("You'll never pay a late fee with us.", "PROMISE-001"),
            ("You'll never miss out on a refund with us, I promise.", "PROMISE-001"),
            ("The fee will definitely be gone by Friday.", "SLA-001"),
            ("I will definitely be able to fix it, and it will definitely be done.", "SLA-001"),
        ],
    )
    async def test_the_keyword_outside_the_idiom_still_fires(self, text, rule_id):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context={"account_tenure_days": 400}
        )
        assert [v.rule_id for v in violations] == [rule_id]

    async def test_the_rewrite_keeps_an_idiom_sentence(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "Promo codes don't last forever. Your price, though, is locked in forever.",
            {"account_tenure_days": 400},
        )
        assert result.corrected_text.startswith("Promo codes don't last forever. ")
        assert "locked in" not in result.corrected_text
