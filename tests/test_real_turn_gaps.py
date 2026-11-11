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
            # function words added after the last review: la, de, tu, ihr
            ("La entrega de your order llega hoy with the app", "es"),
            ("Tu pedido de The New You llega hoy", "es"),
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
        assert detect_language("Please call Oscar de la Renta about your order") == "en"

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
            "Citi credit card ending 4417",  # "I" inside a word is not a verb cue
            # words that can stand before the noun are not verb cues
            "We accept cash and credit card for orders over $600.",
            "Please charge me credit card fees of $600 only once.",
            "We now credit card payments differently",
            "You should credit card companies with that idea",
            "debit and credit cards accepted",
            "We charged your credit card ending 4417 for $600.",
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
            "Let me credit card ending 4417",
            # the verb, known by what follows it
            "I'll gladly credit card ending 4417 with $600.",
            "and credit cards ending 4417 and 5521 with $40 each",
            "Then we simply credit card ending in 4417 with 600 dollars",
            # ...or by more words before it
            "I'd credit card ending 4417",
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
            "We accept cash and credit card for orders over $600.",
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
            "I'll gladly credit card ending 4417 with $90.",
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
    """REFUND-001 is excused only when the whole reply is questions, refusals and neutral remarks."""

    @pytest.mark.parametrize(
        "text",
        [
            "So you'd like a refund for the headphones?",
            "You're after a refund on the blender, is that right?",
            "You want a refund on the blender, correct?",
            "I can look up the status of your refund for you.",
            "I'm sorry, I can't refund this order.",
            "Could you tell me why you'd like the refund?",
            "We're not able to accept the return after 30 days.",
            "Is this about our refund policy or about a specific order?",
            "Have you been waiting for the refund long?",
            "Are you requesting a refund for both items?",
            "Are you asking for a refund on both orders?",
            "We are unable to refund opened items.",
            "We won't accept the return without a receipt.",
            "I can’t refund this order.",  # curly apostrophe
            "I'll check the status of your refund.",  # the agent acts, but only looks
            "Let me take a look at your refund request.",
            "Thanks for waiting. Unfortunately we can't refund opened items.",
            "Hi Dana, I'm sorry, we're unable to refund this one.",
            "I apologise for the delay, but I cannot process a refund on this order.",
            "Unfortunately I can't refund this order. Is there anything else I can help with?",
            "- Sorry\n- we can't refund it",
        ],
    )
    async def test_replies_that_grant_nothing_are_allowed(self, text):
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
            "How should we send your refund, back to your card or as store credit?",
            "Can I offer you a refund instead?",
            "Your earlier refund is still pending, and I've also issued a refund for the second pair.",
            "Would you like me to issue the refund today?",
            "I will process your refund right away.",
            "Your refund is on its way.",
            "You are eligible for a refund, so I have started it.",
            # questions that take the refund as settled or propose it
            "Would you accept a refund of the delivery fee?",
            "Should I send the refund to a gift card?",
            "Is the refund method the same card?",
            "Which card should the refund go to?",
            "How about a full refund?",
            "Want me to refund it?",
            "Where should the refund go?",
            "Do you want the refund on the card ending 4417?",
            "Should the refund go to your PayPal or your bank?",
            "Would you like your money back?",
            "Do you want a refund or a replacement?",
            "Why not take a refund instead?",
            # a question excuses itself only
            "I'll refund you now, ok?",
            "You'll get your money back, ok?",
            "The blender is refunded, was there something else?",
            "I have refunded your order. anything else?",
            "You'd like a refund? Sure thing.",
            "You'd like a refund? Done.",
            # the agent acts, in any tense
            "I've approved your refund request.",
            "I'm requesting a refund from finance now.",
            "We processed the refund request you sent on Monday.",
            "I put your refund request through.",
            "About your refund request, I processed it this morning.",
            "You requested a refund, so I sent $80 to your card.",
            "Since you requested a refund I've gone ahead and issued it.",
            "Per our refund policy you're entitled to a full refund, which I've issued.",
            # nobody acts, but the refund happens
            "Refunds will be issued to your card today.",
            "Your refund is now complete.",
            "Your refund request has been paid out.",
            "Your refund request is granted.",
            "Refund approved,",
            "We're honoring your refund request.",
            "You're outside the refund window, but it's yours anyway.",
            "Your refund has been completed and you'll see it in 3 days.",
            # a refusal excuses its own part of the sentence only
            "I can't process this as an exchange so I'll refund it instead.",
            "I can’t exchange it, so here is a refund.",
            "I can't refund the shipping fee, just the $80 for the item itself.",
            "I can't refund this to PayPal, so a check for the full amount is in the mail.",
            "I can't normally refund orders this old, but I've made it happen for you.",
            "I can't refund more than the $80 you paid.",
            # a negative that means eagerness is not a refusal
            "I can't wait to refund you for this.",
            "There's no reason I can't refund this order.",
            "I won't hesitate to refund this.",
            # looking something up, then reporting an outcome
            "I'll check with my manager who approved your refund.",
            "I see your refund request. I've approved it in full.",
            "Sure, I'll refund the full amount,",
        ],
    )
    async def test_grants_still_fire(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert [v.rule_id for v in violations] == ["REFUND-001"]

    async def test_made_up_late_refunds_written_to_break_the_rule_all_fire(self):
        """104 replies that grant a late refund, written by a reviewer against 943cd22.

        The rule before v1.6 flagged all of them, 943cd22 flagged 47 and the
        last form that looked for the agent acting flagged 103.
        """
        import json
        from pathlib import Path

        probes = json.loads(
            (Path(__file__).parent / "fixtures" / "refund_grant_probes.json").read_text("utf-8")
        )
        store = KnowledgeGraphStore()
        missed = [
            text
            for text in probes
            if not await store.check_policy_compliance(
                text=text, intent_type="general", customer_context=LATE_PURCHASE
            )
        ]
        assert len(probes) == 104
        assert missed == []

    def test_only_a_confirmation_tag_makes_the_whole_sentence_a_question(self):
        assert not grants_refund("You want a refund on the blender, correct?", ["refund"])
        assert grants_refund("The blender is refunded, was there something else?", ["refund"])
        assert grants_refund("You'll get a refund, ok?", ["refund"])

    def test_trailing_marks_after_the_question_mark_do_not_hide_it(self):
        assert not grants_refund("So you'd like the refund on the lamp?>", ["refund"])
        assert not grants_refund("So you'd like the refund on the lamp?!", ["refund"])

    def test_a_reply_without_the_keyword_is_not_this_rules_business(self):
        assert not grants_refund("I've sent it to your card today.", ["refund"])

    @pytest.mark.parametrize(
        "text",
        [
            # status reports and statements of policy: nothing is granted, and the
            # rule fires anyway, as it did before v1.6
            "That refund is still pending, it normally lands within a week.",
            "Premium members get unlimited refunds on every order.",
            "Refunds are only available for orders that are still in our warehouse.",
            "I see you requested a refund last Tuesday.",
            "It is still inside the refund window.",
            "The refund is not started on our side.",
            "We have not received your refund request.",
            "I've checked and your refund is still pending.",
            "Refunds can be issued once the parcel is scanned.",
            # a refusal next to a remark that is not on the neutral list
            "I'm sorry, I can't refund this order. The 30-day window closed on the 3rd.",
            # a question that only looks like an offer
            "Do you want the refund on the lamp?",
        ],
    )
    async def test_harmless_replies_that_still_fire_are_the_known_cost(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context=LATE_PURCHASE
        )
        assert [v.rule_id for v in violations] == ["REFUND-001"]

    def test_the_check_is_linear_on_long_input(self):
        import time

        for text in (
            "refund" + " \t" * 20000 + "and",
            "i can not " * 4000 + "refund",
            "sorry, " * 5700 + "refund",
            "let me check " * 3000 + "refund",
        ):
            start = time.perf_counter()
            grants_refund(text, ["refund"])
            assert time.perf_counter() - start < 0.5

    async def test_the_rewrite_drops_a_status_sentence_that_would_confirm_the_refund(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "I've issued your refund. Your refund is now complete.", LATE_PURCHASE
        )
        assert "complete" not in result.corrected_text
        assert "refund" not in result.corrected_text.lower()


class TestIdiomsThatContainAKeyword:
    """A keyword inside an idiom is not what PROMISE-001 or SLA-001 is about."""

    @pytest.mark.parametrize(
        "text",
        [
            "Promo codes don't last forever, so use yours this month.",
            "That price won't last forever.",
            "Sign up for alerts and you'll never miss a clearance event.",
            "We will definitely be able to swap the size before it ships.",
            "Our billing team will definitely be able to glance at the invoice.",
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
            ("You'll never miss out on this rate, it is locked in.", "PROMISE-001"),
            ("The tech will definitely be able to fix it at 9am sharp.", "SLA-001"),
            ("The fee will definitely be gone by Friday.", "SLA-001"),
            ("I will definitely be able to fix this by Friday.", "SLA-001"),
            ("We will definitely be able to sort it within 2 days.", "SLA-001"),
            ("I will definitely be able to fix it, and it will definitely be done.", "SLA-001"),
            # a time anywhere in the sentence, before or after the idiom
            ("By Friday we will definitely be able to deliver it.", "SLA-001"),
            ("We will definitely be able to ship it before the weekend.", "SLA-001"),
            ("We will definitely be able to sort this out next month.", "SLA-001"),
            ("This afternoon we will definitely be able to call you back.", "SLA-001"),
            # no longer blanked: only a sale or an event can be the thing not missed
            ("You'll never miss a delivery again, I've made sure of it.", "PROMISE-001"),
            ("The trial is not forever.", "PROMISE-001"),
        ],
    )
    async def test_the_keyword_outside_the_idiom_still_fires(self, text, rule_id):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context={"account_tenure_days": 400}
        )
        assert [v.rule_id for v in violations] == [rule_id]

    def test_blanking_keeps_offsets_and_is_linear(self):
        import time

        from autocorrector.knowledge_graph.rules import without_idioms

        text = "We will definitely be able to help. By 5pm it will definitely be able to ship."
        blanked = without_idioms("SLA-001", text)
        assert len(blanked) == len(text)
        assert blanked.startswith("We" + " " * 28 + "help. By 5pm it will definitely")
        start = time.perf_counter()
        without_idioms("SLA-001", "will definitely be able to " * 1500)
        without_idioms("PROMISE-001", "you'll never miss a " * 2000)
        assert time.perf_counter() - start < 0.5

    async def test_the_rewrite_keeps_an_idiom_sentence(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        result = await run_autocorrection(
            "Promo codes don't last forever. Your price, though, is locked in forever.",
            {"account_tenure_days": 400},
        )
        assert result.corrected_text.startswith("Promo codes don't last forever. ")
        assert "locked in" not in result.corrected_text
