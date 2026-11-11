"""Rule keywords match the word or phrase itself, not longer words that start with it.

Found after v1.5: "sue" matched the name "Sue" and "suede", so a shipping update
was escalated as a legal threat. ``scripts/audit_keywords.py`` lists every such
collision against a dictionary; these tests pin the ones a reply could contain.
"""

import importlib.util
from pathlib import Path

import pytest

from autocorrector.knowledge_graph.keywords import mentions_keyword
from autocorrector.knowledge_graph.store import KnowledgeGraphStore

SCRIPT = Path(__file__).parent.parent / "scripts" / "audit_keywords.py"
ESTABLISHED = {"account_tenure_days": 200}


async def _rule_ids(text: str, context: dict | None = None) -> list[str]:
    violations = await KnowledgeGraphStore().check_policy_compliance(
        text=text, intent_type="general", customer_context=context or ESTABLISHED
    )
    return [v.rule_id for v in violations]


class TestLongerWordsDoNotMatch:
    @pytest.mark.parametrize(
        "text",
        [
            "Thanks, Sue. Your order shipped this morning.",
            "Hi Sue, the replacement is on its way.",
            "Those suede boots are back in stock.",
            "I have escalated the issue to our billing team.",
            "We are always willing to help with that.",
            "You'll nevertheless need to send us the receipt.",
            "The agreement between you and Medicare covers that visit.",
        ],
    )
    async def test_benign_reply_has_no_violation(self, text):
        assert await _rule_ids(text) == []

    async def test_creditor_is_not_a_credit_offer(self):
        text = "Your creditor may add a $75 late fee to the balance."
        assert await _rule_ids(text, {"account_tenure_days": 10}) == []


class TestIntendedFormsStillMatch:
    @pytest.mark.parametrize(
        ("text", "context", "expected"),
        [
            ("I will sue you", ESTABLISHED, ["ESC-001"]),
            ("If you sue us, our attorneys will respond.", ESTABLISHED, ["ESC-001"]),
            ("Sue us if you want, the fee stands.", ESTABLISHED, ["ESC-001"]),
            ("You said you are suing the company.", ESTABLISHED, ["ESC-001"]),
            ("The customer sued last year.", ESTABLISHED, ["ESC-001"]),
            ("I've credited $80 to your account.", {"account_tenure_days": 10}, ["CREDIT-001"]),
            ("I've added two credits of $80.", {"account_tenure_days": 10}, ["CREDIT-001"]),
            (
                "Your order is fully refundable, I'll start it now.",
                {"account_tenure_days": 200, "days_since_purchase": 45},
                ["REFUND-001"],
            ),
            ("It will be fixed by next weekend.", ESTABLISHED, ["SLA-001"]),
            ("We track that in our internal tools.", ESTABLISHED, ["DISC-001"]),
            ("I can make policy exceptions for you.", ESTABLISHED, ["REFUND-002"]),
        ],
    )
    async def test_rule_still_fires(self, text, context, expected):
        assert await _rule_ids(text, context) == expected


class TestMentionsKeyword:
    def test_word_start_is_required(self):
        assert not mentions_keyword("we raised the issue", ["sue"])

    def test_word_end_is_required(self):
        assert not mentions_keyword("suede boots", ["sue"])
        assert not mentions_keyword("the creditor called", ["credit"])

    def test_single_word_takes_inflections(self):
        for form in ("credits", "credited", "crediting", "the credit's value"):
            assert mentions_keyword(form, ["credit"]), form
        assert mentions_keyword("they are suing", ["sue"])

    def test_phrase_takes_a_plural_only(self):
        assert mentions_keyword("our internal tools", ["internal tool"])
        assert mentions_keyword("internal processes", ["internal process"])
        assert not mentions_keyword("always willing", ["always will"])

    def test_upper_and_lower_case_are_the_verb(self):
        assert mentions_keyword("I WILL SUE", ["sue"])
        assert mentions_keyword("then sue us", ["sue"])

    def test_match_is_case_insensitive(self):
        assert mentions_keyword("A full REFUND is on its way", ["refund"])


class TestNameOrVerb:
    """A capitalised "Sue" is a name only in a greeting; anything else is the verb."""

    @pytest.mark.parametrize(
        "text",
        [
            "Sue if you want, the fee stands.",
            "Sue my company then.",
            "Sue all you like.",
            "Sue for damages if you think you have a case.",
            "You are free to Sue.",
            "I'll Sue",
            "I Will Sue You",
            "You Can Sue Us Anytime",
            "If you want, Sue us.",
            "Sue in small claims court if you like.",
            "They Sued us last year.",
            "I'm sorry. Sue us if you must.",
            "Thanks. Sue us then.",
            "Sorry, Sue us.",
            "That's just how I am. Sue me.",
            "Sue, if you must.",
            "Sue, then.",
            "You can Sue, but the fee stands.",
            "Sue is your only option then.",
            "Sue has always been your right.",
            "If you disagree, Sue.",
            "Sue, go ahead.",
            "Sue, or pay the fee.",
            "You can Sue, no problem.",
            "We Will Sue, Says Customer",
            "I Will Sue From Now On",
            "Please do not Sue, we can fix this.",
            "Thanks, Sue the company then.",
            "OK, Sue the company then.",
            "Sorry, Sue our parent company instead.",
            "Sue's your best option.",
            "Thanks\nSue the company",
        ],
    )
    def test_threat_is_the_verb(self, text):
        assert mentions_keyword(text, ["sue"])

    @pytest.mark.parametrize(
        "text",
        [
            "Thanks, Sue.",
            "Thank you, Sue!",
            "Thanks Sue!",
            "Hi Sue, the replacement is on its way.",
            "Dear Sue,\nyour claim is approved.",
            "Good morning Ms. Sue, your claim is approved.",
            "Regards,\nSue",
        ],
    )
    def test_greeting_is_not_the_verb(self, text):
        assert not mentions_keyword(text, ["sue"])

    @pytest.mark.parametrize(
        "text",
        [
            "Sue, your order shipped.",
            "Sue's replacement is on its way.",
            "I asked Sue this morning.",
            "Sue will call you back.",
            "Sue from billing has the details.",
            "Please ask for Sue",
        ],
    )
    def test_name_outside_a_greeting_still_fires(self, text):
        # Known false positives, kept on purpose: see keywords.py
        assert mentions_keyword(text, ["sue"])


class TestSuffixRules:
    def test_d_follows_e_and_es_follows_a_sibilant(self):
        assert not mentions_keyword("a card", ["car"])
        assert not mentions_keyword("it cares", ["car"])
        assert not mentions_keyword("a band", ["ban"])
        assert mentions_keyword("two cars", ["car"])
        assert mentions_keyword("two boxes", ["box"])
        assert mentions_keyword("it was banned", ["bann"])


class TestFormsKeptByExplicitKeywords:
    @pytest.mark.parametrize(
        ("text", "context", "expected"),
        [
            (
                "I can offer your account a $100 bonus.",
                {"account_tenure_days": 10},
                ["CREDIT-001"],
            ),
            ("Our internal tooling shows the outage.", ESTABLISHED, ["DISC-001"]),
            ("Our internal processing takes three days.", ESTABLISHED, ["DISC-001"]),
            (
                "I can accept the returned item for you.",
                {"account_tenure_days": 200, "days_since_purchase": 45},
                ["REFUND-001"],
            ),
            (
                "I'll process your returned order today.",
                {"account_tenure_days": 200, "days_since_purchase": 45},
                ["REFUND-001"],
            ),
        ],
    )
    async def test_rule_still_fires(self, text, context, expected):
        assert await _rule_ids(text, context) == expected


class TestRewriterUsesTheSameMatcher:
    async def test_sentence_with_a_longer_word_is_kept(self):
        from autocorrector.orchestrator.pipeline import run_autocorrection

        text = "I promise this is fixed. We are always willing to help."
        result = await run_autocorrection(text, customer_context=ESTABLISHED)
        assert result.action_taken.value == "correct"
        assert result.corrected_text.endswith("We are always willing to help.")

    async def test_amount_next_to_a_longer_word_is_not_capped(self):
        from autocorrector.knowledge_graph.claims import assign_amounts

        text = "I've added a credit for you. Your creditor may add a $75 late fee."
        assert assign_amounts(text, {"offer": ["credit"]})["offer"] == []


class TestCoverageMentions:
    """Coverage categories are matched as whole words too; aliases keep the variants."""

    @pytest.mark.parametrize(
        "text",
        ["Your specialist copayment is $60.", "Specialist copayments are $60 each."],
    )
    async def test_copayment_wording_is_still_fact_checked(self, text):
        errors = await KnowledgeGraphStore().verify_facts(
            text=text, customer_context={"plan_type": "standard_2024"}
        )
        assert [e.is_accurate for e in errors] == [False]


class TestAuditScript:
    def _load(self):
        spec = importlib.util.spec_from_file_location("audit_keywords", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_no_default_keyword_matches_a_longer_word(self):
        words = [
            "suede", "suet", "creditor", "creditable", "willing", "nevertheless",
            "meanwhile", "medicare", "everyone", "weekday", "toolbox", "policyholder",
            "beach", "thanks", "background", "against", "your", "hourly",
        ]  # fmt: skip
        report = self._load().audit(words)
        assert [found for found, _ in report.values() if found] == []

    def test_matches_separates_collisions_from_lookalikes(self):
        audit = self._load()
        # "credit-card" on its own counts: the compound is skipped only after a word
        # that marks a noun ("a credit-card payment"), which a word list never has
        assert audit.matches("credit", ["credit-union", "credit-card", "credited", "creditor"]) == (
            ["credit-union", "credit-card"],
            ["credited"],
        )
        assert audit.matches("us", ["used", "usher"]) == ([], ["used"])
        assert audit.inflections("sue") >= {"sues", "sued", "suing"}
