"""Behaviour gaps the held-out run exposed (docs/evaluation.md), fixed with new cases.

Each test pins the mechanism behind one gap. The held-out cases themselves are
never copied here; the golden rows GAP-001..011 carry the end-to-end checks.
"""

import pytest

from autocorrector.guardrails.validators import find_pii_spans
from autocorrector.knowledge_graph.store import KnowledgeGraphStore


class TestRulesAcrossDomains:
    """A rule fires on its own triggers whatever intent the classifier picked."""

    @pytest.mark.parametrize(
        ("text", "intent_type", "context", "expected"),
        [
            (
                "Sure, I can get your money back on the blender you ordered in September.",
                "general",
                {"account_tenure_days": 200, "days_since_purchase": 45},
                ["REFUND-001"],
            ),
            (
                "I can override the policy and waive the late fee this once.",
                "financial_compensation",
                {"account_tenure_days": 180},
                ["REFUND-002"],
            ),
        ],
    )
    async def test_refund_rules_fire_under_other_intents(
        self, text, intent_type, context, expected
    ):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type=intent_type, customer_context=context
        )
        assert [v.rule_id for v in violations] == expected

    @pytest.mark.parametrize(
        "text",
        [
            "The system will override the old address once you confirm the new one.",
            "I'll return your call as soon as the technician has an update.",
        ],
    )
    async def test_generic_words_do_not_trigger_refund_rules(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="general", customer_context={"account_tenure_days": 200}
        )
        assert violations == []

    async def test_regulator_triggers_escalation_rule(self):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="You've contacted the state regulator, so I've flagged your case.",
            intent_type="general",
            customer_context={"account_tenure_days": 180},
        )
        assert [v.rule_id for v in violations] == ["ESC-001"]


class TestDateOfBirthSpans:
    @pytest.mark.parametrize(
        "text",
        [
            "I've confirmed the date of birth you gave us, 04/17/1986, matches our records.",
            "Your DOB on file is 04/17/1986.",
            "You were born on 17-04-1986 according to the application.",
        ],
    )
    def test_dob_found_a_few_words_after_its_cue(self, text):
        assert [kind for kind, _, _ in find_pii_spans(text)] == ["date_of_birth"]

    def test_date_without_a_cue_is_not_a_dob(self):
        assert find_pii_spans("Your order shipped on 04/17/2026.") == []


class TestOtherPlanClaims:
    """An amount attributed to another plan is checked against that plan's limits."""

    async def test_correct_comparison_passes(self):
        errors = await KnowledgeGraphStore().verify_facts(
            text=(
                "Emergency dental is $5,000 on the Premium plan, while your Standard plan "
                "covers up to $2,500."
            ),
            customer_context={"plan_type": "standard_2024"},
        )
        assert errors == []

    async def test_wrong_limit_for_the_other_plan_is_flagged_with_its_source(self):
        errors = await KnowledgeGraphStore().verify_facts(
            text=(
                "The Premium plan covers emergency dental up to $6,000, while your Standard "
                "plan covers up to $2,500."
            ),
            customer_context={"plan_type": "standard_2024"},
        )
        assert [(e.claim, e.correct_value, e.source_document) for e in errors] == [
            ("$6,000", "$5,000", "Policy_Document_Premium_2024.pdf")
        ]

    async def test_unnamed_plan_still_means_the_customers_plan(self):
        errors = await KnowledgeGraphStore().verify_facts(
            text="Your emergency dental coverage is $5,000.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # The only plan named sits across a connector: "your plan" is the customer's
            (
                "The Premium plan covers emergency dental up to $5,000, while your plan "
                "covers up to $2,500.",
                [],
            ),
            ("Unlike the Premium plan, your coverage for emergency dental is $2,500.", []),
            (
                "The Premium plan covers emergency dental up to $5,000, while your plan "
                "covers up to $3,000.",
                [("$3,000", "$2,500")],
            ),
        ],
    )
    async def test_other_plan_across_a_connector_does_not_claim_the_amount(self, text, expected):
        errors = await KnowledgeGraphStore().verify_facts(
            text=text, customer_context={"plan_type": "standard_2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == expected

    async def test_plan_type_in_display_form_is_checked(self):
        errors = await KnowledgeGraphStore().verify_facts(
            text="Your emergency dental coverage is $5,000.",
            customer_context={"plan_type": "Standard 2024"},
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    def test_claim_for_a_plan_without_coverages_here_is_left_alone(self):
        store = KnowledgeGraphStore()
        facts = store._load_default_facts()["standard_2024"]["coverages"]
        errors = store._fact_errors(
            "The Premium plan covers emergency dental up to $6,000.",
            facts,
            "Standard 2024",
            {"premium_2024": None},
        )
        assert errors == []


class TestOfferAmountsTypedByAFollowingNoun:
    @pytest.mark.parametrize(
        ("text", "claimed"),
        [
            ("I can apply a $700 emergency room credit to your account.", []),
            ("I'll put $650 toward your emergency dental bill.", []),
            ("Emergency dental charges of up to $5,000 are covered.", ["5,000"]),
        ],
    )
    def test_amount_before_a_category_that_a_money_word_follows(self, text, claimed):
        from autocorrector.knowledge_graph.claims import assign_amounts

        found = assign_amounts(text, KnowledgeGraphStore().coverage_mentions())
        assert [m.group(1) for cat in found for m in found[cat]] == claimed


class TestRefundDenials:
    @pytest.mark.parametrize(
        "text",
        [
            "I can't offer a refund, but I can send a replacement.",
            "Refunds are not possible after 30 days, sorry.",
        ],
    )
    async def test_refund_denial_past_the_window_is_not_a_violation(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text,
            intent_type="general",
            customer_context={"account_tenure_days": 200, "days_since_purchase": 45},
        )
        assert violations == []


class TestDateOfBirthStaysInItsSentence:
    @pytest.mark.parametrize(
        "text",
        [
            "We verified your date of birth. Your order shipped on 04/17/2026.",
            "DOB verified. Ship date 03/01/2026.",
        ],
    )
    def test_date_in_the_next_sentence_is_not_a_dob(self, text):
        assert find_pii_spans(text) == []
