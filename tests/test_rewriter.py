"""Tests for span-level deterministic rewrites (knowledge_graph/rewriter.py)."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from autocorrector.knowledge_graph.rewriter import RewriteResult, fact_edits, rewrite
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import CorrectionAction, CorrectionEdit, FactCheckResult
from autocorrector.orchestrator import pipeline
from autocorrector.orchestrator.pipeline import run_autocorrection

CREDIT_TAIL = " If you need more than that, I can ask a supervisor to review it."

# rule_id -> (intent type the rule applies to, customer context that triggers it)
RULE_SETUP = {
    "CREDIT-001": ("financial_compensation", {"account_tenure_days": 45}),
    "REFUND-001": ("refund_processing", {"days_since_purchase": 45}),
    "PROMISE-001": ("general", {}),
    "SLA-001": ("general", {}),
    "DISC-001": ("general", {}),
}

# rule_id -> (agent text, expected rewrite)
REWRITES = {
    "CREDIT-001": (
        "Sorry about the outage. I can offer you a $200 credit for the inconvenience.",
        "Sorry about the outage. I can offer you a $50 credit for the inconvenience." + CREDIT_TAIL,
    ),
    "REFUND-001": (
        "Absolutely, I'll process a full refund for you today. Is there anything else?",
        "This purchase is past our 30-day window, but let me check what options are "
        "available. Is there anything else?",
    ),
    "PROMISE-001": (
        "I promise this will never happen again. Thanks for your patience.",
        "I can't make a permanent commitment, but I'll make sure this is handled now. "
        "Thanks for your patience.",
    ),
    "SLA-001": (
        "Your issue will be fixed within 24 hours. Thanks for waiting.",
        "I'll prioritize this and keep you updated on progress. Thanks for waiting.",
    ),
    "DISC-001": (
        "Let me check that. Our internal system shows a flag on your account. "
        "I'll get back to you shortly.",
        "Let me check that. I'll get back to you shortly.",
    ),
}


@pytest.fixture
def store():
    return KnowledgeGraphStore()


async def _violations(store, text, rule_id):
    intent_type, context = RULE_SETUP[rule_id]
    found = await store.check_policy_compliance(text, intent_type, context)
    return [v.model_dump() for v in found]


class TestRewriteRules:
    @pytest.mark.parametrize("rule_id", sorted(REWRITES))
    async def test_rewrite_rule_exact_output(self, store, rule_id):
        text, expected = REWRITES[rule_id]
        violations = await _violations(store, text, rule_id)
        assert [v["rule_id"] for v in violations] == [rule_id]

        result = rewrite(text, violations, store.rule_index())

        assert result.text == expected
        assert result.unresolved == ()
        assert {e.rule_id for e in result.edits} == {rule_id}
        assert all(e.tier == "span" for e in result.edits)

    @pytest.mark.parametrize("rule_id", sorted(REWRITES))
    async def test_rewrite_output_revalidates_clean(self, store, rule_id):
        text, _ = REWRITES[rule_id]
        result = rewrite(text, await _violations(store, text, rule_id), store.rule_index())
        intent_type, context = RULE_SETUP[rule_id]
        again = await store.check_policy_compliance(result.text, intent_type, context)
        assert [v.rule_id for v in again] == []

    async def test_rewrite_credit_edit_cites_offsets_and_source(self, store):
        text, expected = REWRITES["CREDIT-001"]
        result = rewrite(text, await _violations(store, text, "CREDIT-001"), store.rule_index())
        (edit,) = result.edits
        assert edit.before == "I can offer you a $200 credit for the inconvenience."
        assert edit.after == "I can offer you a $50 credit for the inconvenience." + CREDIT_TAIL
        assert text[edit.start : edit.end] == edit.before
        assert edit.start == len("Sorry about the outage. ")
        assert edit.source_document == "Credit_Policy_2024_v3.2"
        assert edit.source_section == "Section 4.1"

    async def test_rewrite_disc_drop_records_empty_after(self, store):
        text, _ = REWRITES["DISC-001"]
        result = rewrite(text, await _violations(store, text, "DISC-001"), store.rule_index())
        (edit,) = result.edits
        assert edit.before == "Our internal system shows a flag on your account."
        assert edit.after == ""
        assert text[edit.start : edit.end] == edit.before

    async def test_rewrite_replaces_first_flagged_sentence_and_drops_later_ones(self, store):
        text = "I'll process your refund now. The refund will post in five days. Anything else?"
        result = rewrite(text, await _violations(store, text, "REFUND-001"), store.rule_index())
        assert result.text == (
            "This purchase is past our 30-day window, but let me check what options are "
            "available. Anything else?"
        )
        assert [e.after for e in result.edits] == [result.text.removesuffix(" Anything else?"), ""]

    async def test_rewrite_credit_and_promise_compose(self, store):
        text = "I can offer you a $200 credit today. I promise this will never happen again."
        violations = await store.check_policy_compliance(
            text, "financial_compensation", {"account_tenure_days": 45}
        )
        assert {v.rule_id for v in violations} == {"CREDIT-001", "PROMISE-001"}

        result = rewrite(text, [v.model_dump() for v in violations], store.rule_index())

        assert result.text == (
            "I can offer you a $50 credit today."
            + CREDIT_TAIL
            + " I can't make a permanent commitment, but I'll make sure this is handled now."
        )
        assert [e.rule_id for e in result.edits] == ["CREDIT-001", "PROMISE-001"]

    async def test_rewrite_timeline_guarantee_prefers_sla_wording(self, store):
        """One sentence flagged by PROMISE-001 and SLA-001 gets the more specific SLA rewrite."""
        text = "I guarantee this will be resolved within 24 hours."
        violations = await store.check_policy_compliance(text, "general", {})
        assert [v.rule_id for v in violations] == ["PROMISE-001", "SLA-001"]

        result = rewrite(text, [v.model_dump() for v in violations], store.rule_index())

        assert result.text == "I'll prioritize this and keep you updated on progress."
        (edit,) = result.edits
        assert edit.rule_id == "SLA-001, PROMISE-001"
        assert edit.source_document == "SLA_Policy_2024_v1.0; Agent_Conduct_Policy_2024_v1.0"
        assert result.unresolved == ()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "I can offer you a $200 credit and I promise this will never happen again.",
                "I can offer you a $50 credit." + CREDIT_TAIL + " I can't make a permanent "
                "commitment, but I'll make sure this is handled now.",
            ),
            (
                "Off the record, I can offer you a $200 credit.",
                "I can offer you a $50 credit." + CREDIT_TAIL,
            ),
            (
                "I can offer you $80 or a $150 credit, your choice.",
                "I can offer you a $50 credit." + CREDIT_TAIL,
            ),
        ],
    )
    async def test_rewrite_credit_composes_within_sentence(self, store, text, expected):
        """A capped credit that shares its sentence with another fix becomes a standalone offer."""
        violations = await store.check_policy_compliance(
            text, "financial_compensation", {"account_tenure_days": 45}
        )
        result = rewrite(text, [v.model_dump() for v in violations], store.rule_index())

        assert result.text == expected
        assert result.unresolved == ()
        again = await store.check_policy_compliance(
            result.text, "financial_compensation", {"account_tenure_days": 45}
        )
        assert again == []

    async def test_rewrite_keeps_redacted_context(self, store):
        text = "Your SSN [SSN REDACTED] is verified and I can offer you a $200 credit."
        result = rewrite(text, await _violations(store, text, "CREDIT-001"), store.rule_index())
        assert result.text == (
            "Your SSN [SSN REDACTED] is verified and I can offer you a $50 credit." + CREDIT_TAIL
        )

    async def test_rewrite_word_amount_left_unresolved(self, store):
        """No `$` amount to edit: the rule falls through to the full template."""
        text = "I can offer you a two hundred dollar credit today."
        result = rewrite(text, await _violations(store, text, "CREDIT-001"), store.rule_index())
        assert result.unresolved == ("CREDIT-001",)
        assert result.edits == ()
        assert result.text == text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "I can offer you a $100 credit and your emergency dental coverage is $5,000.",
                "I can offer you a $50 credit and your emergency dental coverage is $5,000.",
            ),
            (
                "I can offer you a $100 credit (that's on top of the $20 we gave you).",
                "I can offer you a $50 credit (that's on top of the $20 we gave you).",
            ),
        ],
    )
    async def test_rewrite_credit_caps_only_the_offer(self, store, text, expected):
        """Other amounts in the sentence (coverage, earlier credits) are kept, not restated away."""
        rule = store.rule_index()["CREDIT-001"]
        violation = {k: rule[k] for k in ("rule_id", "source_document", "source_section")}
        result = rewrite(text, [violation], store.rule_index(), store.coverage_mentions())
        assert result.text == expected + CREDIT_TAIL

    async def test_rewrite_disc_everything_dropped_left_unresolved(self, store):
        text = "Off the record, our CRM flagged this."
        result = rewrite(text, await _violations(store, text, "DISC-001"), store.rule_index())
        assert result.unresolved == ("DISC-001",)

    async def test_rewrite_critical_rule_left_unresolved(self, store):
        text = "I can offer you a $750 credit for the inconvenience."
        violations = await store.check_policy_compliance(
            text, "financial_compensation", {"account_tenure_days": 200}
        )
        result = rewrite(text, [v.model_dump() for v in violations], store.rule_index())
        assert "CREDIT-002" in result.unresolved


class TestStoreTiers:
    async def test_span_tier_used_before_template(self, store):
        text, expected = REWRITES["SLA-001"]
        violations = await _violations(store, text, "SLA-001")
        result = await store.generate_compliant_alternative_with_edits(
            text, violations, "general", {}
        )
        assert result.text == expected
        assert await store.generate_compliant_alternative(text, violations, "general", {}) == (
            expected
        )

    async def test_template_fallback_when_no_span(self, store):
        text = "Off the record, our CRM flagged this."
        violations = await _violations(store, text, "DISC-001")
        result = await store.generate_compliant_alternative_with_edits(
            text, violations, "general", {}
        )
        assert result.text == (
            "Let me look into that for you. I'll work on getting this resolved "
            "and follow up with the next steps."
        )
        (edit,) = result.edits
        assert (edit.tier, edit.rule_id, edit.before, edit.start, edit.end) == (
            "template",
            "DISC-001",
            text,
            0,
            len(text),
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Your bill was $120. I can offer you a $80 credit.",
            "I can offer you a $100 credit (that's on top of the $20 we gave you).",
        ],
    )
    async def test_span_result_that_still_violates_falls_back_to_template(self, store, text):
        """The span tier only resolves a rule if its rewrite no longer triggers that rule."""
        context = {"account_tenure_days": 30}
        violations = await store.check_policy_compliance(text, "financial_compensation", context)
        assert [v.rule_id for v in violations] == ["CREDIT-001"]

        result = await store.generate_compliant_alternative_with_edits(
            text, [v.model_dump() for v in violations], "financial_compensation", context
        )

        (edit,) = result.edits
        assert (edit.tier, edit.rule_id) == ("template", "CREDIT-001")
        assert result.text == edit.after

    async def test_neo4j_tier_records_whole_text_edit(self, store, monkeypatch):
        text, violations = await self._credit_case(store)
        store._connected = True
        monkeypatch.setattr(store, "_get_correction_from_neo4j", AsyncMock(return_value="Fixed."))

        result = await store.generate_compliant_alternative_with_edits(
            text, violations, "financial_compensation", {"account_tenure_days": 45}
        )

        assert result.text == "Fixed."
        (edit,) = result.edits
        assert (edit.tier, edit.rule_id, edit.before, edit.after) == (
            "neo4j",
            "CREDIT-001",
            text,
            "Fixed.",
        )
        assert (edit.start, edit.end) == (0, len(text))
        assert (edit.source_document, edit.source_section) == (
            "Credit_Policy_2024_v3.2",
            "Section 4.1",
        )

    async def test_llm_tier_records_whole_text_edit_citing_all_rules(self, store, monkeypatch):
        text = "I can offer you a $200 credit today. I promise this will never happen again."
        context = {"account_tenure_days": 45}
        violations = [
            v.model_dump()
            for v in await store.check_policy_compliance(text, "financial_compensation", context)
        ]
        monkeypatch.setattr(store, "_get_llm_correction", AsyncMock(return_value="LLM fix."))

        result = await store.generate_compliant_alternative_with_edits(
            text, violations, "financial_compensation", context
        )

        (edit,) = result.edits
        assert (edit.tier, edit.rule_id, edit.before, edit.after) == (
            "llm",
            "CREDIT-001, PROMISE-001",
            text,
            "LLM fix.",
        )
        assert (edit.start, edit.end, edit.source_document) == (0, len(text), None)

    async def test_cache_hit_returns_same_result(self, store):
        text, violations = await self._credit_case(store)
        args = (text, violations, "financial_compensation", {"account_tenure_days": 45})
        first = await store.generate_compliant_alternative_with_edits(*args)
        second = await store.generate_compliant_alternative_with_edits(*args)
        assert second is first
        assert second.edits == first.edits

    async def test_store_caps_offer_without_restating_away_coverage(self, store):
        """The store's rewrite keeps the coverage figure; the rule check still counts it."""
        text = "I can offer you a $100 credit and your emergency dental coverage is $300."
        violation = {"rule_id": "CREDIT-001", "source_document": "Credit_Policy_2024_v3.2"}
        result = await store.generate_compliant_alternative_with_edits(
            text, [violation], "financial_compensation", {"account_tenure_days": 30}
        )
        (edit,) = result.edits
        assert edit.tier == "template"

    async def _credit_case(self, store):
        text = "I can offer you a $200 credit for the inconvenience."
        found = await store.check_policy_compliance(
            text, "financial_compensation", {"account_tenure_days": 45}
        )
        return text, [v.model_dump() for v in found]


class TestFactCorrections:
    def _dental_error(self):
        return FactCheckResult(
            claim="$5,000",
            is_accurate=False,
            correct_value="$2,500",
            source_document="Policy_Document_Standard_2024.pdf",
            source_page=12,
            confidence=0.99,
        )

    def test_fact_correction_records_edit(self):
        text = "Your Standard plan covers up to $5,000 in emergency dental procedures."
        result = fact_edits(text, [self._dental_error()])
        assert result.text == (
            "Your Standard plan covers up to $2,500 in emergency dental procedures."
        )
        (edit,) = result.edits
        assert (edit.rule_id, edit.before, edit.after) == ("FACT-CHECK", "$5,000", "$2,500")
        assert text[edit.start : edit.end] == "$5,000"
        assert edit.source_document == "Policy_Document_Standard_2024.pdf"
        assert edit.source_section == "p. 12"

    def test_fact_correction_does_not_touch_longer_amounts(self):
        error = self._dental_error().model_copy(update={"claim": "$50", "correct_value": "$40"})
        result = fact_edits("A $500 deductible and a $50 copay.", [error])
        assert result.text == "A $500 deductible and a $40 copay."

    def test_correction_edit_is_immutable(self):
        """Cached edits are shared between results, so they cannot be changed in place."""
        edit = CorrectionEdit(rule_id="FACT-CHECK", before="$5,000", after="$2,500", start=0, end=6)
        with pytest.raises(ValidationError):
            edit.after = "$9,999"


class TestPipelineEdits:
    async def test_pipeline_records_edits(self):
        result = await run_autocorrection(
            text="I can offer you a $200 credit for the inconvenience.",
            customer_context={"account_tenure_days": 45},
        )
        assert result.action_taken == CorrectionAction.CORRECT
        assert result.corrected_text == (
            "I can offer you a $50 credit for the inconvenience." + CREDIT_TAIL
        )
        assert [e.rule_id for e in result.edits] == ["CREDIT-001"]

    async def test_pipeline_records_fact_edits(self):
        result = await run_autocorrection(
            text="Your Standard plan covers up to $5,000 in emergency dental procedures.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 365},
        )
        assert [(e.rule_id, e.before, e.after) for e in result.edits] == [
            ("FACT-CHECK", "$5,000", "$2,500")
        ]

    async def test_pipeline_records_pii_types(self):
        result = await run_autocorrection(
            text="Your SSN 123-45-6789 is on file; we emailed john.doe@example.com.",
            customer_context={},
        )
        assert result.pii_detected is True
        assert result.pii_types == ["ssn", "email"]

    async def test_pipeline_escalation_has_no_edits(self):
        result = await run_autocorrection(
            text="I can offer you a $750 credit for the inconvenience.",
            customer_context={"account_tenure_days": 200},
        )
        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.edits == []

    async def test_pipeline_edits_accumulate_across_attempts(self, monkeypatch):
        """Each correction attempt adds its edits after the previous attempt's."""
        store = KnowledgeGraphStore()
        await store.connect()
        first = CorrectionEdit(rule_id="CREDIT-001", before="a", after="b", start=0, end=1)
        second = CorrectionEdit(rule_id="CREDIT-001", before="c", after="d", start=0, end=1)
        attempts = iter(
            [
                RewriteResult(text="I can offer you a $60 credit.", edits=(first,)),
                RewriteResult(text="I can offer you a $40 credit.", edits=(second,)),
            ]
        )

        async def two_step_rewrite(original_text, *args, **kwargs):
            return next(attempts)

        monkeypatch.setattr(store, "generate_compliant_alternative_with_edits", two_step_rewrite)
        monkeypatch.setattr(pipeline, "_kg_store", store)
        result = await run_autocorrection(
            text="I can offer you a $200 credit for the inconvenience.",
            customer_context={"account_tenure_days": 45},
        )
        assert result.corrected_text == "I can offer you a $40 credit."
        assert result.edits == [first, second]

    async def test_pipeline_escalation_after_correction_drops_edits(self, monkeypatch):
        """A rewrite that revalidates as critical escalates; its edits were never sent."""
        store = KnowledgeGraphStore()
        await store.connect()
        edit = CorrectionEdit(rule_id="CREDIT-001", before="x", after="y", start=0, end=1)

        async def bad_rewrite(original_text, *args, **kwargs):
            return RewriteResult(text="I'll apply a $1,000 credit today.", edits=(edit,))

        monkeypatch.setattr(store, "generate_compliant_alternative_with_edits", bad_rewrite)
        monkeypatch.setattr(pipeline, "_kg_store", store)
        result = await run_autocorrection(
            text="I can offer you a $200 credit for the inconvenience.",
            customer_context={"account_tenure_days": 45},
        )
        assert result.action_taken == CorrectionAction.ESCALATE
        assert result.edits == []
