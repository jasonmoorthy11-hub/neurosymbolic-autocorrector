"""Tests for claim extraction (knowledge_graph/claims.py)."""

import time

import pytest
from pydantic import ValidationError

from autocorrector.knowledge_graph.claims import (
    apply_fact_corrections,
    assign_amounts,
    sentence_spans,
)
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import FactCheckResult


def _categories() -> dict[str, list[str]]:
    aliases = KnowledgeGraphStore._CATEGORY_ALIASES
    facts = KnowledgeGraphStore._load_default_facts()["standard_2024"]["coverages"]
    return {f["category"]: [f["category"], *aliases.get(f["category"], [])] for f in facts}


def _claims(text: str) -> dict[str, list[str]]:
    """Category -> claimed amounts, omitting categories with no claim."""
    assigned = assign_amounts(text, _categories())
    return {cat: [m.group(1) for m in found] for cat, found in assigned.items() if found}


class TestSentenceSpans:
    @pytest.mark.parametrize(
        ("text", "sentences"),
        [
            ("One. Two! Three?", ["One.", "Two!", "Three?"]),
            ("Your coverage, e.g. root canals, is $5,000.", None),
            ("Your limit is approx. $5,000 a year.", None),
            ("It is $2,500.00. Anything else?", ["It is $2,500.00.", "Anything else?"]),
            ("- Dental: $2,500\n- Room: $10,000", ["- Dental: $2,500", "- Room: $10,000"]),
            ("Done. $5,000 is the cap.", ["Done.", "$5,000 is the cap."]),
            # a period before a lowercase word is not a sentence end
            ("See p. ten of the plan for details.", None),
        ],
    )
    def test_sentence_spans_boundaries(self, text, sentences):
        found = [text[s:e] for s, e in sentence_spans(text)]
        assert found == (sentences or [text])


class TestAssignAmounts:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # adjacency: each amount belongs to the mention it sits next to
            (
                "Your $40 specialist copay and $2,500 emergency dental limit are unchanged.",
                {"specialist visit copay": ["40"], "emergency dental": ["2,500"]},
            ),
            (
                "Under your plan, emergency dental is $5,000 and emergency room is $15,000.",
                {"emergency dental": ["5,000"], "emergency room": ["15,000"]},
            ),
            # "$X for <category>": a gap containing "and" loses to one that doesn't
            (
                "The limit is $2,500 for emergency dental and $10,000 for emergency room.",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            (
                "Emergency dental (up to $2,500) and emergency room (up to $10,000).",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            # list items and line breaks are separate clauses
            (
                "- Emergency dental: $2,500\n- Emergency room coverage: $10,000",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            (
                "Your emergency dental limit is $2,500\nand ER visits are covered to $10,000",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            (
                "Prescription drugs: $5,000; emergency dental: $2,500.",
                {"prescription drugs": ["5,000"], "emergency dental": ["2,500"]},
            ),
            # abbreviations do not end the sentence
            (
                "Your emergency dental coverage, e.g. root canals, is $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            ("Your emergency dental limit is approx. $5,000.", {"emergency dental": ["5,000"]}),
            # a lone mention claims amounts anywhere in its clause
            (
                "Your plan includes emergency dental coverage for accidents and urgent "
                "procedures, with a maximum annual benefit of $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            # "It ..." refers back to the single category of the previous sentence
            (
                "Your plan covers emergency dental. It pays up to $5,000 per year.",
                {"emergency dental": ["5,000"]},
            ),
            # ...but an unrelated next sentence does not
            (
                "Your emergency dental limit is unchanged. I can offer you a $200 credit.",
                {},
            ),
            # alias "er visit" is a whole-word match, not inside "never visited"
            ("Since you never visited us before, we sent you $300.", {}),
            ("It is $2,500.00 for emergency dental.", {"emergency dental": ["2,500.00"]}),
            # a semicolon ends the clause, so the lone mention can't reach past it
            ("Your emergency dental claim is approved; we received $45 today.", {}),
            # equal gaps, no connector: the mention before the amount wins
            (
                "Emergency dental $2,500 prescription drugs $5,000.",
                {"emergency dental": ["2,500"], "prescription drugs": ["5,000"]},
            ),
            # a relative clause (commas) loses only to "and", not to a shorter comma gap
            (
                "Your emergency dental coverage, which includes root canals, is $2,500, "
                "and your emergency room coverage is $10,000.",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            (
                "For emergency dental, coverage is $2,500, and for emergency room, $10,000.",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            # amounts typed by a non-coverage money word are not coverage claims
            ("Your emergency dental claim was approved, and a $45 copay applies.", {}),
            ("Emergency dental work has a fee of $30 at this clinic.", {}),
            ("Your specialist copay is $60.", {"specialist visit copay": ["60"]}),
            ("Your $60 specialist copay applies.", {"specialist visit copay": ["60"]}),
            # ...but a money word right after the mention describes the category
            (
                "We'll cover emergency dental charges of up to $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Emergency dental bills of up to $5,000 are covered.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Your plan makes emergency dental payments of up to $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            # intended: a remaining balance or a deductible is not the plan limit
            ("Your emergency dental balance is $5,000.", {}),
            ("Your emergency dental deductible is $500.", {}),
            ("Emergency dental fees of up to $5,000 are covered.", {"emergency dental": ["5,000"]}),
            ("Emergency dental: $5,000 balance available.", {}),
            # referring back needs a coverage verb, and typed amounts never refer back
            ("We've updated your emergency dental claim. It qualifies for a $200 credit.", {}),
            ("Your emergency room visit has been processed. This means you owe $150.", {}),
            ("Your emergency room visit has been processed. This is a $150 copay.", {}),
            ("Your emergency dental limit is unchanged. They will refund the $5,000 deposit.", {}),
            ("Your plan covers emergency dental. It includes a $25 fee per visit.", {}),
            (
                "Your plan covers emergency dental. It's capped at $5,000 a year.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Your plan covers emergency dental. It’s capped at $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Your plan covers emergency dental. The annual maximum is $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Your plan covers emergency dental. It has a $5,000 annual cap.",
                {"emergency dental": ["5,000"]},
            ),
            (
                "Your plan covers emergency dental. It'll pay up to $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            ("Your plan covers emergency dental. It has a $200 credit attached.", {}),
            # bills, fees and payments are not limits unless limit language says so
            ("Your emergency room bill was $1,200.", {}),
            ("Your emergency dental fee is $150.", {}),
            ("Emergency dental fees of $150 apply per visit.", {}),
            ("Emergency room charges for your visit were $1,200.", {}),
            ("Your emergency dental payment of $480 has been processed.", {}),
            ("We received the emergency dental invoice for $480.", {}),
            (
                "Emergency dental bills are covered up to $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            # a money word across a connector does not type the amount
            (
                "Emergency dental has no deductible and covers up to $5,000.",
                {"emergency dental": ["5,000"]},
            ),
            # payouts referred back to are not limits: referring back needs limit language
            (
                "Your emergency room claim is processed. The benefit check of $1,200 is in the "
                "mail.",
                {},
            ),
            (
                "Your emergency dental claim was approved. The coverage team will mail you a "
                "$200 check.",
                {},
            ),
            ("Your emergency dental claim was approved. It has a $200 reimbursement pending.", {}),
            ("Your emergency room visit is covered. It will cover the $1,500 you paid.", {}),
            (
                "Your emergency dental claim was approved. It pays $350 directly to your dentist.",
                {},
            ),
            ("Your emergency dental claim was approved. That includes the $320 for the crown.", {}),
            # "The <noun> team" is not "The limit is": limit language alone is not enough
            (
                "Your emergency dental claim was approved. The benefit team approved $480 for "
                "your annual cleaning.",
                {},
            ),
            # a typed amount never refers back, even with limit language
            ("Your plan covers emergency dental. It has an annual $50 fee.", {}),
            # "check" is a payout, not a limit
            ("Your emergency dental claim was approved, and a check for $480 is on its way.", {}),
            # amounts used or left of a benefit are usage, not the limit
            ("You've used $480 of your emergency dental limit.", {}),
            ("You have $2,020 remaining in your emergency dental benefit.", {}),
            (
                "Your emergency dental claim was approved. That leaves $2,020 of your annual "
                "limit.",
                {},
            ),
            (
                "Your emergency dental claim was approved. This year you've used $480 of your "
                "annual benefit.",
                {},
            ),
            # a comma-free gap beats a shorter gap across a comma
            (
                "Emergency dental is $2,500, emergency room is $10,000.",
                {"emergency dental": ["2,500"], "emergency room": ["10,000"]},
            ),
            # a label line continues on the next line
            ("Emergency dental:\n$5,000", {"emergency dental": ["5,000"]}),
            # known limitation (same as before this change): an untyped unrelated amount
            # in a lone-mention clause is still read as that category's claim
            (
                "Your emergency dental benefit sits alongside a separate $5,000 orthodontic "
                "allowance.",
                {"emergency dental": ["5,000"]},
            ),
        ],
    )
    def test_assign_amounts_cases(self, text, expected):
        assert _claims(text) == expected

    def test_assign_amounts_each_amount_claimed_once(self):
        text = "Emergency dental coverage for emergency dental work is $5,000."
        assert _claims(text) == {"emergency dental": ["5,000"]}

    @pytest.mark.parametrize(
        "text",
        [
            "emergency dental $1 " * 1000,  # ~20 KB, one clause, mentions and amounts interleaved
            "emergency dental " + "covers $1 " * 2000,  # one mention, many amounts
            "emergency dental, " * 600 + "$1 " * 600,  # many commas and mentions before amounts
        ],
        ids=["interleaved", "one-mention", "commas"],
    )
    def test_assign_amounts_long_input_is_fast(self, text):
        """Assignment must stay near-linear: it runs inline on the event loop."""
        started = time.perf_counter()
        _claims(text)
        assert time.perf_counter() - started < 0.5


class TestApplyFactCorrections:
    def _error(self, claim, correct, start=None, end=None):
        return FactCheckResult(
            claim=claim,
            is_accurate=False,
            correct_value=correct,
            confidence=0.99,
            claim_start=start,
            claim_end=end,
        )

    def test_apply_fact_corrections_by_span(self):
        text = "Your $5,000 prescription drug limit and $5,000 emergency dental limit stay."
        start = text.rindex("$5,000")
        fixed = apply_fact_corrections(text, [self._error("$5,000", "$2,500", start, start + 6)])
        assert (
            fixed == "Your $5,000 prescription drug limit and $2,500 emergency dental limit stay."
        )

    def test_apply_fact_corrections_without_span_matches_whole_amounts(self):
        fixed = apply_fact_corrections(
            "A $500 deductible and a $50 copay.", [self._error("$50", "$40")]
        )
        assert fixed == "A $500 deductible and a $40 copay."

    @pytest.mark.parametrize("tail", [", which is great.", ".", " a year."])
    def test_apply_fact_corrections_fallback_before_punctuation(self, tail):
        fixed = apply_fact_corrections(
            "Emergency dental covers up to $5,000" + tail, [self._error("$5,000", "$2,500")]
        )
        assert fixed == "Emergency dental covers up to $2,500" + tail

    @pytest.mark.parametrize(("start", "end"), [(-6, 6), (0, -1)])
    def test_fact_check_result_rejects_negative_offsets(self, start, end):
        with pytest.raises(ValidationError):
            self._error("$5,000", "$2,500", start, end)

    def test_apply_fact_corrections_overlapping_spans_keep_one(self):
        """Two claims over the same amount: only one replacement lands."""
        fixed = apply_fact_corrections(
            "Up to $5,000.00 here.",
            [
                self._error("$5,000.00", "$2,500.00", 6, 15),
                self._error("$5,000", "$9,999", 6, 12),
            ],
        )
        assert fixed == "Up to $2,500.00 here."

    def test_apply_fact_corrections_stale_span_falls_back(self):
        """A span that no longer points at the claim (text changed) is not trusted."""
        fixed = apply_fact_corrections(
            "Up to $5,000 for emergency dental.", [self._error("$5,000", "$2,500", 0, 6)]
        )
        assert fixed == "Up to $2,500 for emergency dental."
