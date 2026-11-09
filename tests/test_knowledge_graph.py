"""Tests for the knowledge graph store (in-memory mode)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction
from autocorrector.orchestrator.pipeline import run_autocorrection


class TestPolicyCompliance:
    @pytest.mark.asyncio
    async def test_credit_over_threshold_new_account(self, connected_kg_store):
        """Canonical scenario 1: $200 credit with 45-day tenure triggers violation."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $200 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        assert len(violations) == 1
        assert violations[0].rule_id == "CREDIT-001"

    @pytest.mark.asyncio
    async def test_credit_under_threshold_ok(self, connected_kg_store):
        """Small credit under $50 should not trigger CREDIT-001."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $25 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        credit_001 = [v for v in violations if v.rule_id == "CREDIT-001"]
        assert len(credit_001) == 0

    @pytest.mark.asyncio
    async def test_credit_with_sufficient_tenure(self, connected_kg_store):
        """Credit over $50 but with 90+ day tenure should not trigger CREDIT-001."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $200 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 120},
        )
        credit_001 = [v for v in violations if v.rule_id == "CREDIT-001"]
        assert len(credit_001) == 0

    @pytest.mark.asyncio
    async def test_excessive_credit_triggers_critical(self, connected_kg_store):
        """Credit over $500 triggers critical CREDIT-002 regardless of tenure."""
        violations = await connected_kg_store.check_policy_compliance(
            text="I can offer you a $750 credit for the inconvenience.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 200},
        )
        rule_ids = [v.rule_id for v in violations]
        assert "CREDIT-002" in rule_ids

    @pytest.mark.asyncio
    async def test_no_violations_for_unrelated_intent(self, connected_kg_store):
        """Text about troubleshooting should not trigger financial rules."""
        violations = await connected_kg_store.check_policy_compliance(
            text="Please try restarting your router.",
            intent_type="troubleshooting",
            customer_context={"account_tenure_days": 45},
        )
        assert len(violations) == 0


class TestFactVerification:
    @pytest.mark.asyncio
    async def test_dental_coverage_overstatement(self, connected_kg_store):
        """Canonical scenario 2: $5,000 dental claim on Standard plan (actual $2,500)."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $5,000 in emergency dental work.",
            customer_context={"plan_type": "standard_2024"},
        )
        inaccurate = [e for e in errors if not e.is_accurate]
        assert len(inaccurate) == 1
        assert "$2,500" in inaccurate[0].correct_value

    @pytest.mark.asyncio
    async def test_correct_coverage_passes(self, connected_kg_store):
        """Correct $2,500 dental claim on Standard plan should pass."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $2,500 in emergency dental work.",
            customer_context={"plan_type": "standard_2024"},
        )
        inaccurate = [e for e in errors if not e.is_accurate]
        assert len(inaccurate) == 0

    @pytest.mark.asyncio
    async def test_no_fact_check_without_plan(self, connected_kg_store):
        """Without plan context, fact checking should return empty."""
        errors = await connected_kg_store.verify_facts(
            text="Your plan covers up to $5,000 in emergency dental work.",
            customer_context={},
        )
        assert len(errors) == 0


class TestCorrectionGeneration:
    @pytest.mark.asyncio
    async def test_credit_correction_generated(self, connected_kg_store):
        """Correction for CREDIT-001 should suggest $50 and escalation."""
        corrected = await connected_kg_store.generate_compliant_alternative(
            original_text="I can offer you a $200 credit for the inconvenience.",
            violations=[
                {
                    "rule_id": "CREDIT-001",
                    "rule_name": "Credit Authorization Threshold",
                    "description": "Credits over $50 require 90+ days tenure",
                    "severity": "high",
                }
            ],
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 45},
        )
        assert "$50" in corrected
        assert "supervisor" in corrected.lower() or "escalate" in corrected.lower()

    @pytest.mark.asyncio
    async def test_compliant_text_unchanged(self, connected_kg_store):
        """Text with no matching violations should remain unchanged."""
        original = "Thank you for calling. How can I help you?"
        corrected = await connected_kg_store.generate_compliant_alternative(
            original_text=original,
            violations=[],
            intent_type="general",
            customer_context={},
        )
        assert corrected == original


def _neo4j_store(records: list[dict]) -> KnowledgeGraphStore:
    """A store that behaves as connected, with one Neo4j query returning ``records``."""
    mock_records = []
    for data in records:
        rec = MagicMock()
        rec.data.return_value = data
        mock_records.append(rec)

    mock_result = MagicMock()
    mock_result.__aiter__ = lambda self: self
    mock_result.__anext__ = AsyncMock(side_effect=[*mock_records, StopAsyncIteration])

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    store = KnowledgeGraphStore()
    store._connected = True
    store._driver = MagicMock()
    store._driver.session.return_value = mock_session
    return store


_STANDARD_COVERAGES = [
    {
        "plan": "standard_2024",
        "category": cov["category"],
        "coverage_limit": cov["limit"],
        "source_doc": cov["source_document"],
        "source_page": cov["source_page"],
    }
    for cov in KnowledgeGraphStore._load_default_facts()["standard_2024"]["coverages"]
]

_UNRELATED_AMOUNTS = "Your $40 specialist copay and $2,500 emergency dental limit are unchanged."
_DENTAL_OVERSTATED = "Your Standard plan covers up to $5,000 in emergency dental procedures."


class TestFactCheckAdjacency:
    async def test_fact_check_ignores_unrelated_amounts(self, connected_kg_store):
        """Each amount is checked only against the category it sits next to."""
        errors = await connected_kg_store.verify_facts(
            text=_UNRELATED_AMOUNTS, customer_context={"plan_type": "standard_2024"}
        )
        assert errors == []

    async def test_fact_check_still_flags_adjacent_overstatement(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "standard_2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    async def test_fact_check_ignores_amounts_in_other_sentences(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text="Your emergency dental limit is unchanged. I can offer you a $200 credit.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert errors == []

    async def test_fact_check_two_categories_one_sentence(self, connected_kg_store):
        errors = await connected_kg_store.verify_facts(
            text="Under your plan, emergency dental is $5,000 and emergency room is $10,000.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]

    async def test_fact_check_neo4j_ignores_unrelated_amounts(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text=_UNRELATED_AMOUNTS, customer_context={"plan_type": "Standard 2024"}
        )
        assert errors == []

    async def test_fact_check_reports_claim_span(self, connected_kg_store):
        text = "Your $5,000 prescription drug limit and $5,000 emergency dental limit stay."
        errors = await connected_kg_store.verify_facts(
            text=text, customer_context={"plan_type": "standard_2024"}
        )
        (error,) = errors
        assert (error.claim, error.correct_value) == ("$5,000", "$2,500")
        start = text.rindex("$5,000")
        assert (error.claim_start, error.claim_end) == (start, start + len("$5,000"))

    async def test_fact_check_neo4j_uses_aliases(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text="Your specialist copay is $60.", customer_context={"plan_type": "Standard 2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$60", "$40")]

    @pytest.mark.parametrize(
        "text",
        [
            "Emergency dental on Premium is $10,000.",
            "Emergency dental under your Premium is $10,000.",
        ],
    )
    async def test_fact_check_premium_plan_name_is_not_a_money_word(self, connected_kg_store, text):
        errors = await connected_kg_store.verify_facts(
            text=text, customer_context={"plan_type": "premium_2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$10,000", "$5,000")]

    async def test_fact_check_neo4j_flags_adjacent_overstatement(self):
        store = _neo4j_store(_STANDARD_COVERAGES)
        errors = await store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "Standard 2024"}
        )
        assert [(e.claim, e.correct_value) for e in errors] == [("$5,000", "$2,500")]


class TestMissingContextParity:
    async def test_missing_context_same_in_neo4j_and_memory(self):
        """A missing tenure field counts as 0 days in both paths (D-213)."""
        text = "I can offer you a $200 credit for the inconvenience."
        credit_rule = {
            "rule_id": "CREDIT-001",
            "name": "Credit Authorization Threshold",
            "description": "Credits over $50 require 90+ days tenure",
            "severity": "high",
            "source_document": "Credit_Policy_2024_v3.2",
            "source_section": "Section 4.1",
            "amount_threshold": 50,
            "keywords": ["credit"],
            "conditions": [{"field": "account_tenure_days", "operator": "lt", "value": 90}],
        }
        neo4j_violations = await _neo4j_store([credit_rule]).check_policy_compliance(
            text=text, intent_type="financial_compensation", customer_context={}
        )
        memory_violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="financial_compensation", customer_context={}
        )
        assert [v.rule_id for v in memory_violations] == ["CREDIT-001"]
        assert [v.rule_id for v in neo4j_violations] == ["CREDIT-001"]


_COVERAGE_AND_CREDIT = "Your emergency dental is $2,500. I can offer you a $50 credit."


_CREDIT_002 = {
    "rule_id": "CREDIT-002",
    "name": "Credit Supervisor Threshold",
    "description": "Single credit cannot exceed $500 without supervisor approval",
    "severity": "critical",
    "source_document": "Credit_Policy_2024_v3.2",
    "source_section": "Section 4.3",
    "amount_threshold": 500,
    "keywords": KnowledgeGraphStore().rule_index()["CREDIT-002"]["keywords"],
    "conditions": [],
}

# Offers that name a coverage category are still offers: (text, tenure, expected rule ids)
_CATEGORY_NAMED_OFFERS = [
    ("I'm adding a $600 emergency dental credit to your account.", 400, ["CREDIT-002"]),
    ("I can give you a $200 emergency room credit today.", 30, ["CREDIT-001"]),
    (
        "As a courtesy, I'll apply $600 to your emergency dental balance as compensation.",
        400,
        ["CREDIT-002"],
    ),
]


# Offer language in one sentence, the amount (next to a category) in the next
_NEIGHBOUR_KEYWORD_OFFERS = [
    "I'll cover $700 of your emergency room costs. Consider it a goodwill adjustment.",
    "I want to make this right with a credit. I'll put $600 toward your emergency dental.",
    "As compensation, here is what I'll do. I'm applying $600 to your emergency dental.",
    # Exactly the plan's limits: the value alone does not make a coverage statement
    "I want to make this right with a credit. I'll put $2,500 toward your emergency dental.",
    "I want to make this right with a credit. I'll put $10,000 toward your emergency room bill.",
    # Coverage-statement shape, but the sentence moves money to the customer
    "Here is a goodwill adjustment. I'll apply it so your emergency dental limit is $2,500.",
    # No verb or keyword in the amount's sentence, but not shaped as a coverage statement
    "Consider it a goodwill adjustment. Yours today: emergency dental, $2,500.",
    # Statement shape before the amount, a gift after it
    "Here's my compensation offer. The emergency dental benefit of $2,500 is yours.",
    "Here's my compensation offer. Your emergency dental is $2,500, all yours today.",
]
_HIGH_AND_CRITICAL = ["CREDIT-001", "CREDIT-002"]


class TestCreditAmountsIgnoreCoverage:
    """Only a correct coverage statement, in a sentence that offers nothing, is not an offer."""

    @pytest.mark.parametrize(("text", "tenure", "expected"), _CATEGORY_NAMED_OFFERS)
    async def test_credit_rules_count_offer_naming_a_category(self, text, tenure, expected):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": tenure},
        )
        assert [v.rule_id for v in violations] == expected

    @pytest.mark.parametrize(
        "text", [t for t, _, expected in _CATEGORY_NAMED_OFFERS if expected == ["CREDIT-002"]]
    )
    async def test_credit_rules_neo4j_count_offer_naming_a_category(self, text):
        violations = await _neo4j_store([_CREDIT_002]).check_policy_compliance(
            text=text,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 400},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-002"]

    async def test_category_named_credit_escalates_not_fact_corrected(self):
        """A $600 dental credit must not be 'fact-corrected' into a $2,500 credit."""
        result = await run_autocorrection(
            text="I'm adding a $600 emergency dental credit to your account.",
            customer_context={"account_tenure_days": 30, "plan_type": "standard_2024"},
        )
        assert result.action_taken == CorrectionAction.ESCALATE

    @pytest.mark.parametrize(
        ("text", "context", "expected"),
        [
            (text, {"account_tenure_days": 30, "plan_type": "standard_2024"}, _HIGH_AND_CRITICAL)
            for text in _NEIGHBOUR_KEYWORD_OFFERS
        ]
        + [
            (text, {"account_tenure_days": 400}, ["CREDIT-002"])
            for text in _NEIGHBOUR_KEYWORD_OFFERS
        ]
        + [
            (
                "I want to make this right with a credit. I'll put $200 toward your emergency "
                "room visit.",
                {"account_tenure_days": 30},
                ["CREDIT-001"],
            )
        ],
    )
    async def test_credit_rules_count_offer_with_keyword_in_neighbour_sentence(
        self, text, context, expected
    ):
        """Only a correct coverage statement is exempt; these amounts are offers."""
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text, intent_type="financial_compensation", customer_context=context
        )
        assert [v.rule_id for v in violations] == expected

    async def test_credit_rules_count_exact_limit_in_offer_sentence(self):
        """Even the plan's true limit counts when its own sentence offers something."""
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="As a courtesy credit, your emergency dental is $2,500.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 400, "plan_type": "standard_2024"},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-002"]

    @pytest.mark.parametrize(
        "text",
        [
            _COVERAGE_AND_CREDIT,
            "Your emergency dental is $2,500 per year. I can offer you a $50 credit.",
        ],
    )
    async def test_credit_rules_ignore_correct_coverage_amount(self, text):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=text,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30, "plan_type": "standard_2024"},
        )
        assert violations == []

    @pytest.mark.parametrize(
        "context",
        [{"account_tenure_days": 30}, {"account_tenure_days": 30, "plan_type": "premium_2024"}],
    )
    async def test_credit_rules_count_unverified_coverage_amount(self, context):
        """No plan, or not that plan's limit: the amount counts (fail closed, as before)."""
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text=_COVERAGE_AND_CREDIT,
            intent_type="financial_compensation",
            customer_context=context,
        )
        assert [v.rule_id for v in violations] == _HIGH_AND_CRITICAL

    async def test_credit_rules_still_count_offered_amount_beside_coverage(self):
        violations = await KnowledgeGraphStore().check_policy_compliance(
            text="Your emergency dental is $2,500. I can offer you a $200 credit.",
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30, "plan_type": "standard_2024"},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-001"]

    async def test_credit_rules_neo4j_ignore_correct_coverage_amount(self):
        violations = await _neo4j_store([_CREDIT_002]).check_policy_compliance(
            text=_COVERAGE_AND_CREDIT,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30, "plan_type": "standard_2024"},
        )
        assert violations == []

    @pytest.mark.parametrize("text", _NEIGHBOUR_KEYWORD_OFFERS)
    async def test_credit_rules_neo4j_count_offer_with_keyword_in_neighbour_sentence(self, text):
        violations = await _neo4j_store([_CREDIT_002]).check_policy_compliance(
            text=text,
            intent_type="financial_compensation",
            customer_context={"account_tenure_days": 30, "plan_type": "standard_2024"},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-002"]


class TestTemplateTierCitation:
    """The whole-message template cites every rule it replaces, highest severity first."""

    TEXT = (
        "I can offer you a credit of seventy-five dollars and "
        "I promise it will post within 24 hours."
    )
    CTX = {"account_tenure_days": 30}

    async def _rewrite(self, store):
        violations = await store.check_policy_compliance(
            self.TEXT, "financial_compensation", self.CTX
        )
        assert {v.rule_id for v in violations} == {"CREDIT-001", "PROMISE-001", "SLA-001"}
        return await store.generate_compliant_alternative_with_edits(
            self.TEXT, [v.model_dump() for v in violations], "financial_compensation", self.CTX
        )

    async def test_template_edit_cites_every_fired_rule(self, connected_kg_store):
        result = await self._rewrite(connected_kg_store)
        assert len(result.edits) == 1
        edit = result.edits[0]
        assert edit.tier == "template"
        assert edit.rule_id == "CREDIT-001, PROMISE-001, SLA-001"
        assert edit.source_document == (
            "Credit_Policy_2024_v3.2; Agent_Conduct_Policy_2024_v1.0; SLA_Policy_2024_v1.0"
        )
        assert edit.source_section == "Section 4.1; Section 2.3; Section 1.2"
        assert "$50" in result.text

    async def test_template_comes_from_highest_severity_unresolved_rule(self, connected_kg_store):
        """Two unresolved rules, the lower-severity one listed last: its template must not win."""
        violations = [
            {
                "rule_id": "SLA-001",
                "severity": "high",
                "source_document": "SLA_Policy_2024_v1.0",
                "source_section": "Section 1.2",
            },
            {
                "rule_id": "DISC-001",
                "severity": "medium",
                "source_document": "Agent_Conduct_Policy_2024_v1.0",
                "source_section": "Section 5.1",
            },
        ]
        result = await connected_kg_store.generate_compliant_alternative_with_edits(
            "Hello there, thanks for your patience.", violations, "general", {}
        )
        assert [e.tier for e in result.edits] == ["template"]
        assert result.edits[0].rule_id == "SLA-001, DISC-001"
        assert "prioritize" in result.text  # SLA-001's template, not DISC-001's

    async def test_severity_order_puts_critical_first(self, connected_kg_store):
        violations = [
            {"rule_id": "DISC-001", "severity": "medium"},
            {"rule_id": "CREDIT-002", "severity": "critical"},
            {"rule_id": "SLA-001", "severity": "high"},
        ]
        result = await connected_kg_store.generate_compliant_alternative_with_edits(
            "Hello there, thanks for your patience.", violations, "general", {}
        )
        assert result.edits[0].rule_id == "CREDIT-002, SLA-001, DISC-001"
        assert "supervisor" in result.text  # CREDIT-002's template


_ALL_COVERAGES = [
    {
        "plan": plan,
        "category": cov["category"],
        "coverage_limit": cov["limit"],
        "source_doc": cov["source_document"],
        "source_page": cov["source_page"],
    }
    for plan, data in KnowledgeGraphStore._load_default_facts().items()
    for cov in data["coverages"]
]


class TestNeo4jOtherPlanFacts:
    """The Neo4j fact path fetches every plan, so other-plan claims are checked too."""

    async def test_neo4j_checks_claim_attributed_to_another_plan(self):
        store = _neo4j_store(_ALL_COVERAGES)
        results = await store.verify_facts(
            text="The Premium plan covers $10,000 in emergency dental.",
            customer_context={"plan_type": "standard_2024"},
        )
        errors = [r for r in results if not r.is_accurate]
        assert [e.correct_value for e in errors] == ["$5,000"]

    async def test_neo4j_customer_plan_still_checked_by_name(self):
        store = _neo4j_store(_ALL_COVERAGES)
        results = await store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "Standard 2024"}
        )
        assert [r.correct_value for r in results if not r.is_accurate] == ["$2,500"]

    async def test_neo4j_unknown_customer_plan_checks_nothing(self):
        store = _neo4j_store(_ALL_COVERAGES)
        results = await store.verify_facts(
            text=_DENTAL_OVERSTATED, customer_context={"plan_type": "gold_2024"}
        )
        assert results == []


class TestNeo4jSeedParity:
    """The Neo4j seed data is the in-memory data: one source of truth."""

    def _defaults(self):
        return {
            r["rule_id"]: (domain, r)
            for domain, rules in KnowledgeGraphStore._load_default_rules().items()
            for r in rules
        }

    def test_seed_rules_mirror_in_memory_defaults(self):
        from autocorrector.knowledge_graph import schema

        defaults = self._defaults()
        seeded = {r["rule_id"]: r for r in schema.RULES}
        assert len(defaults) == 8
        assert set(seeded) == set(defaults)
        for rule_id, (domain, r) in defaults.items():
            s = seeded[rule_id]
            assert s["name"] == r["rule_name"], rule_id
            assert s["description"] == r["description"], rule_id
            assert s["severity"] == r["severity"], rule_id
            assert s["intent_type"] == domain, rule_id
            assert s["section"] == r["source_section"], rule_id
            assert s["policy"] == r["source_document"], rule_id
            assert s["keywords"] == r["keywords"], rule_id
            assert s["conditions"] == r["conditions"], rule_id
            assert s["amount_threshold"] == r.get("amount_threshold"), rule_id
            assert s["active"] is True, rule_id

    def test_seed_rules_carry_the_template_tier_text(self):
        from autocorrector.knowledge_graph import schema

        store = KnowledgeGraphStore()
        for rule in schema.RULES:
            expected = store._get_template_correction(rule["rule_id"], {}, "")
            assert rule.get("correction_template") == expected, rule["rule_id"]

    def test_seed_policies_cover_every_rule(self):
        from autocorrector.knowledge_graph import schema

        names = {p["name"] for p in schema.POLICIES}
        assert {r["policy"] for r in schema.RULES} <= names

    def test_seed_products_mirror_in_memory_facts(self):
        from autocorrector.knowledge_graph import schema

        facts = KnowledgeGraphStore._load_default_facts()
        assert {p["name"]: p["coverages"] for p in schema.PRODUCTS} == {
            k: v["coverages"] for k, v in facts.items()
        }
        assert {p["name"]: p["display_name"] for p in schema.PRODUCTS} == {
            k: v["plan_name"] for k, v in facts.items()
        }


def _neo4j_template_store(templates: dict[str, str]) -> KnowledgeGraphStore:
    """A connected store whose Neo4j template query answers from ``templates``."""

    async def run(query, **params):
        result = MagicMock()
        template = templates.get(params.get("rule_id", ""))
        record = {"template": template} if template else None
        result.single = AsyncMock(return_value=record)
        return result

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(side_effect=run)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    store = KnowledgeGraphStore()
    store._connected = True
    store._driver = MagicMock()
    store._driver.session.return_value = mock_session
    return store


class TestNeo4jTemplateTierCitation:
    """Tier 1 (Neo4j templates) cites every rule and picks by severity, like tier 4."""

    VIOLATIONS = [
        {
            "rule_id": "SLA-001",
            "severity": "high",
            "source_document": "SLA_Policy_2024_v1.0",
            "source_section": "Section 1.2",
        },
        {
            "rule_id": "CREDIT-002",
            "severity": "critical",
            "source_document": "Credit_Policy_2024_v3.2",
            "source_section": "Section 4.3",
        },
    ]

    async def test_neo4j_template_cites_all_rules_highest_severity_first(self):
        store = _neo4j_template_store({"SLA-001": "SLA wording.", "CREDIT-002": "Credit wording."})
        result = await store.generate_compliant_alternative_with_edits(
            "I guarantee a $750 credit within 24 hours.", self.VIOLATIONS, "general", {}
        )
        (edit,) = result.edits
        assert edit.tier == "neo4j"
        assert result.text == "Credit wording."
        assert edit.rule_id == "CREDIT-002, SLA-001"
        assert edit.source_document == "Credit_Policy_2024_v3.2; SLA_Policy_2024_v1.0"
        assert edit.source_section == "Section 4.3; Section 1.2"

    async def test_neo4j_template_falls_back_to_next_rule_with_a_template(self):
        store = _neo4j_template_store({"SLA-001": "SLA wording."})
        result = await store.generate_compliant_alternative_with_edits(
            "I guarantee a $750 credit within 24 hours.", self.VIOLATIONS, "general", {}
        )
        assert result.text == "SLA wording."
        assert result.edits[0].rule_id == "CREDIT-002, SLA-001"


class TestNeo4jPlanWithoutCoverages:
    async def test_plan_without_coverages_is_known_and_skipped(self):
        """Parity with memory: a claim naming a plan that has no coverage rows is
        skipped, not checked against the customer's plan."""
        records = [
            *_ALL_COVERAGES,
            {
                "plan": "gold_2024",
                "category": None,
                "coverage_limit": None,
                "source_doc": None,
                "source_page": None,
            },
        ]
        store = _neo4j_store(records)
        results = await store.verify_facts(
            text="The Gold plan covers $9,999 in emergency dental.",
            customer_context={"plan_type": "standard_2024"},
        )
        assert [r for r in results if not r.is_accurate] == []


class TestCiteAll:
    def test_empty_rule_ids_are_dropped(self):
        from autocorrector.knowledge_graph.templates import cite_all

        cited = cite_all([{"rule_id": "SLA-001"}, {"rule_id": ""}, {}])
        assert cited["rule_id"] == "SLA-001"


def _neo4j_rules_store() -> KnowledgeGraphStore:
    """A connected store whose rule query serves the seeded RULES (schema.py)."""
    from autocorrector.knowledge_graph.schema import RULES

    rows = [
        {
            "rule_id": r["rule_id"],
            "name": r["name"],
            "description": r["description"],
            "severity": r["severity"],
            "source_document": r["policy"],
            "source_section": r["section"],
            "amount_threshold": r["amount_threshold"],
            "keywords": r["keywords"],
            "intent_type": r["intent_type"],
            # collect() over an OPTIONAL MATCH yields [None] when there are no conditions
            "conditions": r["conditions"] or [None],
        }
        for r in RULES
    ]

    class _Record:
        def __init__(self, data):
            self._data = data

        def data(self):
            return self._data

    class _Result:
        """A fresh async iterator per query (the store queries once per text)."""

        def __aiter__(self):
            async def gen():
                for row in rows:
                    yield _Record(row)

            return gen()

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(side_effect=lambda *a, **k: _Result())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    store = KnowledgeGraphStore()
    store._connected = True
    store._driver = MagicMock()
    store._driver.session.return_value = mock_session
    return store


class TestNeo4jPolicyParity:
    """With the full seed, the Neo4j policy path triggers exactly as the in-memory one."""

    @pytest.mark.parametrize(
        "text, context",
        [
            (
                "I'll do my best to have this resolved within 24 hours, "
                "but I can't guarantee a specific timeline.",
                {},
            ),
            ("The $200 credit was applied to your account last week.", {"account_tenure_days": 10}),
            (
                "Unfortunately a refund is no longer possible since your purchase was 45 days ago.",
                {"days_since_purchase": 45},
            ),
        ],
    )
    async def test_exemptions_apply_on_neo4j(self, text, context):
        store = _neo4j_rules_store()
        assert await store.check_policy_compliance(text, "general", context) == []

    async def test_word_amounts_count_on_neo4j(self):
        store = _neo4j_rules_store()
        violations = await store.check_policy_compliance(
            "I can offer you a credit of seventy-five dollars.",
            "financial_compensation",
            {"account_tenure_days": 30},
        )
        assert [v.rule_id for v in violations] == ["CREDIT-001"]

    async def test_every_golden_case_triggers_the_same_rules(self, connected_kg_store):
        from autocorrector.evaluation.dataset import GOLDEN_DATASET

        neo4j = _neo4j_rules_store()
        mismatches = []
        for case in GOLDEN_DATASET.cases:
            args = (case.agent_text, "general", case.customer_context)
            mem = sorted(v.rule_id for v in await connected_kg_store.check_policy_compliance(*args))
            neo = sorted(v.rule_id for v in await neo4j.check_policy_compliance(*args))
            if mem != neo:
                mismatches.append((case.id, mem, neo))
        assert mismatches == []


class TestActivePolicies:
    async def test_active_policies_groups_rules_by_document(self, kg_store):
        active = await kg_store.get_active_policies()
        by_name = {p["name"]: p["rules"] for p in active["policies"]}
        assert by_name == {
            "Agent_Conduct_Policy_2024_v1.0": ["DISC-001", "PROMISE-001"],
            "Credit_Policy_2024_v3.2": ["CREDIT-001", "CREDIT-002"],
            "Escalation_Policy_2024_v1.0": ["ESC-001"],
            "Refund_Policy_2024_v2.1": ["REFUND-001", "REFUND-002"],
            "SLA_Policy_2024_v1.0": ["SLA-001"],
        }
        assert [p["name"] for p in active["policies"]] == sorted(by_name)
        assert active["total_rules"] == 8
        assert active["total_facts"] == 6
        assert active["store"] == "memory"

    async def test_active_policies_from_a_connected_store(self):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def __aiter__(self):
                async def gen():
                    for row in self._rows:
                        yield row

                return gen()

            async def single(self):
                return self._rows[0] if self._rows else None

        policy_rows = [
            {"name": "Refund_Policy_2024_v2.1", "rules": ["REFUND-002", "REFUND-001"]},
            {"name": "Credit_Policy_2024_v3.2", "rules": ["CREDIT-001"]},
        ]
        results = iter([_Result(policy_rows), _Result([{"total": 4}])])
        mock_session = AsyncMock()
        mock_session.run = AsyncMock(side_effect=lambda *a, **k: next(results))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        store = KnowledgeGraphStore()
        store._connected = True
        store._driver = MagicMock()
        store._driver.session.return_value = mock_session

        active = await store.get_active_policies()

        assert active == {
            "policies": [
                {"name": "Credit_Policy_2024_v3.2", "rules": ["CREDIT-001"]},
                {"name": "Refund_Policy_2024_v2.1", "rules": ["REFUND-001", "REFUND-002"]},
            ],
            "total_rules": 3,
            "total_facts": 4,
            "store": "neo4j",
        }

    async def test_rules_by_domain_cite_their_source(self, kg_store):
        rules = await kg_store.get_rules_by_domain("escalation_request")
        assert rules == [
            {
                "id": "ESC-001",
                "name": "Legal Threat Escalation",
                "description": (
                    "Legal threats or regulatory concerns require immediate supervisor review"
                ),
                "severity": "critical",
                "source_document": "Escalation_Policy_2024_v1.0",
                "source_section": "Section 1.1",
            }
        ]
