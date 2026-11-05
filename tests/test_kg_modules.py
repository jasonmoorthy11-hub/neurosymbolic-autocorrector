"""The knowledge-graph helpers split out of store.py keep their behaviour and names."""

from autocorrector.knowledge_graph import defaults, facts, rules, templates
from autocorrector.knowledge_graph.store import KnowledgeGraphStore


class TestDefaults:
    def test_store_loads_the_module_defaults(self):
        store = KnowledgeGraphStore()
        assert store._policy_rules == defaults.load_default_rules()
        assert store._fact_store == defaults.load_default_facts()

    def test_staticmethods_still_delegate(self):
        assert KnowledgeGraphStore._load_default_rules() == defaults.load_default_rules()
        assert KnowledgeGraphStore._load_default_facts() == defaults.load_default_facts()


class TestTemplates:
    def test_cite_all_joins_ids_and_sources(self):
        cited = templates.cite_all(
            [
                {"rule_id": "CREDIT-002", "source_document": "Credit Policy"},
                {"rule_id": "SLA-001", "source_document": "SLA Policy"},
                {"rule_id": "CREDIT-002", "source_document": "Credit Policy"},
            ]
        )
        assert cited["rule_id"] == "CREDIT-002, SLA-001"
        assert cited["source_document"] == "Credit Policy; SLA Policy"

    def test_by_severity_orders_highest_first(self):
        ordered = templates.by_severity(
            [{"rule_id": "a", "severity": "low"}, {"rule_id": "b", "severity": "critical"}]
        )
        assert [v["rule_id"] for v in ordered] == ["b", "a"]

    def test_whole_text_result_is_one_cited_edit(self):
        result = templates.whole_text_result("old", "new", {"rule_id": "SLA-001"}, "template")
        assert result.text == "new"
        assert result.edits[0].tier == "template"
        assert len(result.edits) == 1
        assert result.edits[0].rule_id == "SLA-001"
        assert (result.edits[0].start, result.edits[0].end) == (0, 3)

    def test_templates_cover_every_correctable_rule(self):
        assert set(templates.CORRECTION_TEMPLATES) == {
            "CREDIT-001",
            "CREDIT-002",
            "REFUND-001",
            "PROMISE-001",
            "SLA-001",
            "DISC-001",
        }


class TestFacts:
    def test_plan_key_normalises_display_names(self):
        assert facts.plan_key("Standard 2024") == "standard_2024"
        assert facts.plan_key(" premium_2024 ") == "premium_2024"

    def test_fact_errors_flags_a_wrong_limit_for_the_customer_plan(self):
        standard = defaults.load_default_facts()["standard_2024"]["coverages"]
        errors = facts.fact_errors(
            "Your emergency dental coverage is $5,000 under the Standard plan.",
            standard,
            "standard_2024",
            {},
            {"standard_2024": ["standard 2024", "standard plan"]},
        )
        assert [e.correct_value for e in errors] == ["$2,500"]

    def test_store_keeps_the_class_alias_and_method(self):
        assert KnowledgeGraphStore._CATEGORY_ALIASES is facts.CATEGORY_ALIASES
        store = KnowledgeGraphStore()
        standard = store._fact_store["standard_2024"]["coverages"]
        errors = store._fact_errors("Your emergency dental is $5,000.", standard, "standard_2024")
        assert [e.correct_value for e in errors] == ["$2,500"]


class TestRules:
    def test_violations_for_is_the_shared_evaluator(self):
        store = KnowledgeGraphStore()
        credit_rules = store._policy_rules["financial_compensation"]
        context = {"account_tenure_days": 30, "plan_type": "standard_2024"}
        text = "I can offer you a $200 credit today."
        direct = rules.violations_for(
            credit_rules,
            text,
            context,
            lambda keywords: store._offer_amounts(text, keywords, context),
        )
        # $200 > CREDIT-001's $50 under 90 days; under CREDIT-002's $500
        assert [v.rule_id for v in direct] == ["CREDIT-001"]
        assert [v.rule_id for v in store._violations_for(credit_rules, text, context)] == [
            "CREDIT-001"
        ]

    def test_tenure_condition_clears_the_credit_rule(self):
        store = KnowledgeGraphStore()
        credit_rules = store._policy_rules["financial_compensation"]
        context = {"account_tenure_days": 400, "plan_type": "standard_2024"}
        text = "I can offer you a $200 credit today."
        direct = rules.violations_for(
            credit_rules,
            text,
            context,
            lambda keywords: store._offer_amounts(text, keywords, context),
        )
        assert direct == []

    def test_offer_amounts_skips_a_verified_coverage_statement(self):
        store = KnowledgeGraphStore()
        limits = {"emergency dental": 2500.0}
        text = "Your emergency dental coverage is $2,500. I can add a $40 credit."
        amounts = rules.offer_amounts(text, ["credit"], limits, store.coverage_mentions())
        assert amounts == ["40"]
