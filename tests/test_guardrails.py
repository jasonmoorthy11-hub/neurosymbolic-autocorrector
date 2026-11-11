"""Tests for Guardrails AI validators and pipeline integration."""

import random

import pytest

from autocorrector.guardrails import validators
from autocorrector.guardrails.telemetry import build_guard, build_pii_guard
from autocorrector.guardrails.validators import (
    ComplianceCheckValidator,
    FactualAccuracyValidator,
    PIIDetectionValidator,
    detect_pii_types,
    find_pii_spans,
    redact_spans,
)
from autocorrector.orchestrator.pipeline import run_autocorrection


def _validator_redaction(text: str) -> str:
    result = PIIDetectionValidator().validate(text)
    return getattr(result, "fix_value", None) or text


def _fuzz_texts(count: int, seed: int = 20261103) -> list[str]:
    rng = random.Random(seed)
    parts = [
        "1234", "567", "8901", "-", ".", " ", "+1", "(", ")", "@", "a", "b.com", "x",
        "DOB ", "born on ", "date of birth ", "/", "passport ", "account number ",
        "routing number ", "DL #", "Main Street", "Ave", ", Columbus, OH 43215",
        "12/31/1990", "123-45-6789", "4111 1111 1111 1111", "jo@example.com",
    ]  # fmt: skip
    return ["".join(rng.choice(parts) for _ in range(rng.randint(1, 12))) for _ in range(count)]


# --- PII Detection Tests ---


class TestPIIDetection:
    """Test PII detection and redaction."""

    def test_ssn_detected_and_redacted(self):
        guard = build_pii_guard()
        result = guard.validate("Your SSN is 123-45-6789.")
        assert result.validation_passed is True  # fixed = passes
        assert "[SSN REDACTED]" in result.validated_output
        assert "123-45-6789" not in result.validated_output

    def test_credit_card_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Card number: 4532-1234-5678-9012")
        assert "[CARD REDACTED]" in result.validated_output
        assert "4532" not in result.validated_output

    def test_email_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Contact me at john.doe@example.com")
        assert "[EMAIL REDACTED]" in result.validated_output
        assert "john.doe@example.com" not in result.validated_output

    def test_phone_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Call us at (555) 123-4567")
        assert "[PHONE REDACTED]" in result.validated_output
        assert "555" not in result.validated_output

    def test_multiple_pii_types(self):
        guard = build_pii_guard()
        result = guard.validate("SSN: 123-45-6789, email: test@test.com, phone: 555-123-4567")
        assert "[SSN REDACTED]" in result.validated_output
        assert "[EMAIL REDACTED]" in result.validated_output
        assert "[PHONE REDACTED]" in result.validated_output

    def test_no_pii_passes(self):
        guard = build_pii_guard()
        result = guard.validate("I can help you with your account.")
        assert result.validation_passed is True
        assert result.validated_output == "I can help you with your account."

    def test_selective_pii_types(self):
        """Test that we can check only specific PII types."""
        guard = build_pii_guard(pii_types=["ssn"])
        result = guard.validate("SSN: 123-45-6789, email: test@test.com")
        assert "[SSN REDACTED]" in result.validated_output
        # Email should NOT be redacted since we only check SSN
        assert "test@test.com" in result.validated_output

    def test_email_regex_does_not_match_pipe(self):
        """Verify email regex [A-Za-z] does not match literal pipe character."""
        guard = build_pii_guard(pii_types=["email"])
        # Pipe character should not be treated as part of TLD
        result = guard.validate("Contact user@example.com for help.")
        assert "[EMAIL REDACTED]" in result.validated_output
        # Ensure a non-email with pipe is NOT matched
        result2 = guard.validate("Use cat|grep to filter output.")
        assert result2.validated_output == "Use cat|grep to filter output."

    def test_phone_with_parentheses(self):
        """Phone numbers starting with ( should be detected."""
        guard = build_pii_guard(pii_types=["phone"])
        result = guard.validate("Call (800) 555-1234 for support.")
        assert "[PHONE REDACTED]" in result.validated_output
        assert "(800)" not in result.validated_output

    def test_phone_with_plus1(self):
        """Phone numbers with +1 prefix should be detected."""
        guard = build_pii_guard(pii_types=["phone"])
        result = guard.validate("Reach us at +1-800-555-1234.")
        assert "[PHONE REDACTED]" in result.validated_output

    def test_dob_detected(self):
        """Date of birth patterns should be detected and redacted."""
        guard = build_pii_guard()
        result = guard.validate("DOB: 01/15/1990 is on file.")
        assert "[DOB REDACTED]" in result.validated_output

    def test_ssn_dot_format(self):
        guard = build_pii_guard()
        result = guard.validate("Your SSN is 412.55.7890 on file.")
        assert "[SSN REDACTED]" in result.validated_output
        assert "412.55.7890" not in result.validated_output

    def test_ssn_space_format(self):
        guard = build_pii_guard()
        result = guard.validate("SSN: 412 55 7890 confirmed.")
        assert "[SSN REDACTED]" in result.validated_output
        assert "412 55 7890" not in result.validated_output

    def test_passport_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your passport number: AB1234567 is on file.")
        assert "[PASSPORT REDACTED]" in result.validated_output
        assert "AB1234567" not in result.validated_output

    def test_passport_with_hash_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("Passport # C9876543 verified.")
        assert "[PASSPORT REDACTED]" in result.validated_output

    def test_bank_account_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your account number: 12345678901 is on file.")
        assert "[BANK ACCOUNT REDACTED]" in result.validated_output
        assert "12345678901" not in result.validated_output

    def test_bank_account_acct_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("Acct# 9876543210 has been updated.")
        assert "[BANK ACCOUNT REDACTED]" in result.validated_output

    def test_routing_number_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your routing number: 021000021 is for Chase.")
        assert "[ROUTING NUMBER REDACTED]" in result.validated_output
        assert "021000021" not in result.validated_output

    def test_routing_aba_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("ABA: 026009593 on your account.")
        assert "[ROUTING NUMBER REDACTED]" in result.validated_output

    def test_drivers_license_detected(self):
        guard = build_pii_guard()
        result = guard.validate("Your driver's license number: D1234567 is verified.")
        assert "[DL REDACTED]" in result.validated_output
        assert "D1234567" not in result.validated_output

    def test_drivers_license_dl_prefix(self):
        guard = build_pii_guard()
        result = guard.validate("DL# S12345678 on file.")
        assert "[DL REDACTED]" in result.validated_output

    def test_address_detected(self):
        guard = build_pii_guard()
        result = guard.validate("We have your address as 123 Main St, Springfield, IL 62704.")
        assert "[ADDRESS REDACTED]" in result.validated_output
        assert "123 Main St" not in result.validated_output

    def test_address_boulevard(self):
        guard = build_pii_guard()
        result = guard.validate("Shipping to 456 Oak Boulevard.")
        assert "[ADDRESS REDACTED]" in result.validated_output

    @pytest.mark.parametrize(
        "text",
        [
            "Your refund of $72 is on its way back to your credit card.",
            "Order 5512 is on its way.",
            "It takes 3 business days each way.",
            "You have 14 days this way.",
            "That is 5 years down the road.",
            "Call 911 in the first place.",
        ],
    )
    def test_a_number_before_an_everyday_phrase_is_not_an_address(self, text):
        assert find_pii_spans(text) == []

    @pytest.mark.parametrize(
        ("text", "span"),
        [
            ("Please ship it to 72 Maple Way, Springfield.", "72 Maple Way"),
            ("we have 123 main st on file", "123 main st"),
            ("It goes to 9 Orchard Court tomorrow.", "9 Orchard Court"),
            # function words are part of a street name before any street type ...
            ("Please send it to 12 the avenue, leeds.", "12 the avenue"),
            ("ok ill ship the replacement to 20 back lane", "20 back lane"),
            ("8 Will Rogers Boulevard", "8 Will Rogers Boulevard"),
            # ... and before "Road", "Court", "Drive" when a name word or a capital is there
            ("your address on file is 9 all saints road", "9 all saints road"),
            ("61 Our Lady Court", "61 Our Lady Court"),
            ("12 The Drive", "12 The Drive"),
            ("12 the Drive", "12 the Drive"),
            # the longest street wins, as before v1.6
            ("It ships to 45 Shady Lane Drive.", "45 Shady Lane Drive"),
            ("1500 East Street Road", "1500 East Street Road"),
            ("8 Mount St Helens Way", "8 Mount St Helens Way"),
            # an everyday noun that is not where the street ends
            ("we deliver to 23 first place drive", "23 first place drive"),
            ("i have 14 back road court on file", "14 back road court"),
            # a city, state and ZIP after it settle the matter
            (
                "send to 12 the drive, Springfield, IL 62704",
                "12 the drive, Springfield, IL 62704",
            ),
        ],
    )
    def test_a_street_address_is_found(self, text, span):
        assert [text[start:end] for _, start, end in find_pii_spans(text)] == [span]

    @pytest.mark.parametrize("text", ["send to 12 the drive", "we are at 4 days road"])
    def test_a_lower_case_address_that_reads_as_a_phrase_is_the_known_loss(self, text):
        assert find_pii_spans(text) == []

    def test_the_address_pattern_is_linear_on_long_runs(self):
        import time

        start = time.perf_counter()
        find_pii_spans("72 " + "is on its " * 10000 + "way")
        find_pii_spans("5 " + "Oak " * 10000)
        find_pii_spans("3 " + "x days " * 6000 + "way")
        assert time.perf_counter() - start < 5  # about 20 ms; quadratic would take minutes

    def test_no_false_positive_on_normal_numbers(self):
        """Normal account references shouldn't trigger bank account detection."""
        guard = build_pii_guard()
        result = guard.validate("Your order #12345 has shipped. Reference case 67890.")
        assert result.validated_output == "Your order #12345 has shipped. Reference case 67890."

    def test_detect_pii_types_in_pattern_order(self):
        text = "Call (555) 123-4567 or email john.doe@example.com; SSN 123-45-6789."
        assert detect_pii_types(text) == ["ssn", "email", "phone"]

    def test_detect_pii_types_none(self):
        assert detect_pii_types("Your order #12345 has shipped.") == []

    def test_find_pii_spans_offsets(self):
        text = "Your SSN is 123-45-6789."
        assert find_pii_spans(text) == [("ssn", 12, 23)]
        assert text[12:23] == "123-45-6789"

    def test_find_pii_spans_sorted_by_start(self):
        text = "Call (555) 123-4567 or email john.doe@example.com; SSN 123-45-6789."
        spans = find_pii_spans(text)
        assert [t for t, _, _ in spans] == ["phone", "email", "ssn"]
        assert [text[s:e] for _, s, e in spans] == [
            "(555) 123-4567",
            "john.doe@example.com",
            "123-45-6789",
        ]

    def test_find_pii_spans_earlier_pattern_claims_overlap(self):
        # Redaction runs in pattern order, so the bank account wins over the phone.
        text = "Refund to account number 5551234567 today."
        assert find_pii_spans(text) == [("bank_account", 10, 35)]

    def test_find_pii_spans_every_match(self):
        text = "Emails: a@example.com and b@example.org"
        assert [t for t, _, _ in find_pii_spans(text)] == ["email", "email"]

    def test_find_pii_spans_case_insensitive(self):
        assert find_pii_spans("dob: 01/02/1990")[0][0] == "date_of_birth"

    def test_find_pii_spans_none(self):
        assert find_pii_spans("Your order #12345 has shipped.") == []

    @pytest.mark.parametrize(
        "text",
        [
            # A later pattern's leftmost match overlaps an earlier claim; re.sub on the
            # redacted text still finds a shorter match.
            "1234567123456781-.a@b.comborn on 45x",
            # A replacement token changes the lookbehind/word-boundary context.
            "a@b.com+11234567123",
            "Call (555) 123-4567 or email john.doe@example.com; SSN 123-45-6789.",
            "Refund to account number 5551234567 today.",
        ],
    )
    def test_find_pii_spans_rebuild_equals_validator(self, text):
        assert redact_spans(text, find_pii_spans(text)) == _validator_redaction(text)

    def test_find_pii_spans_rebuild_equals_validator_fuzz(self):
        mismatches = [
            t
            for t in _fuzz_texts(3000)
            if redact_spans(t, find_pii_spans(t)) != _validator_redaction(t)
        ]
        assert mismatches == []

    def test_find_pii_spans_match_swallowing_a_token_absorbs_its_span(self, monkeypatch):
        # No real pattern matches "[" or "]", so build a later pattern that does.
        monkeypatch.setattr(
            validators,
            "_PII_PATTERNS",
            {"ssn": (r"\d{3}", "[S]"), "email": (r"x\S+y", "[E]")},
        )
        text = "ab x123y cd"
        assert find_pii_spans(text) == [("email", 3, 8)]
        assert redact_spans(text, find_pii_spans(text)) == _validator_redaction(text)

    def test_redact_spans_uses_type_tokens(self):
        assert redact_spans("SSN 123-45-6789", [("ssn", 4, 15)]) == "SSN [SSN REDACTED]"


# --- Compliance Check Tests ---


class TestComplianceCheck:
    """Test policy compliance validation."""

    def test_credit_violation_detected(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I can offer you a $200 credit for the inconvenience.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 30},
            },
        )
        assert "$50" in result.validated_output
        assert (
            "supervisor" in result.validated_output.lower()
            or "escalate" in result.validated_output.lower()
        )

    def test_compliant_credit_passes(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I can offer you a $25 credit for the inconvenience.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 120},
            },
        )
        assert result.validated_output == "I can offer you a $25 credit for the inconvenience."

    def test_critical_credit_detected(self, kg_store):
        guard = build_guard(ComplianceCheckValidator, on_fail="fix")
        result = guard.validate(
            "I'll apply a $750 credit right now.",
            metadata={
                "kg_store": kg_store,
                "intent_type": "financial_compensation",
                "customer_context": {"account_tenure_days": 200},
            },
        )
        # Should be corrected to escalation text
        assert "supervisor" in result.validated_output.lower()


# --- Factual Accuracy Tests ---


class TestFactualAccuracy:
    """Test fact verification validation."""

    def test_wrong_coverage_corrected(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $5,000 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {"plan_type": "standard_2024"},
            },
        )
        assert "$2,500" in result.validated_output
        assert "$5,000" not in result.validated_output

    def test_correct_coverage_passes(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $2,500 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {"plan_type": "standard_2024"},
            },
        )
        assert result.validated_output == "Your plan covers up to $2,500 in emergency dental work."

    def test_no_plan_type_skips_check(self, kg_store):
        guard = build_guard(FactualAccuracyValidator, on_fail="fix")
        result = guard.validate(
            "Your plan covers up to $5,000 in emergency dental work.",
            metadata={
                "kg_store": kg_store,
                "customer_context": {},
            },
        )
        # No plan_type = no fact check = passes through
        assert result.validated_output == "Your plan covers up to $5,000 in emergency dental work."


# --- Pipeline Integration Tests ---


class TestPipelineGuardrails:
    """Test that guardrails are wired into the LangGraph pipeline."""

    @pytest.mark.asyncio
    async def test_pii_redacted_in_pipeline(self):
        """PII in agent output should be redacted before validation."""
        result = await run_autocorrection(
            text="Your SSN 123-45-6789 is on file. I can help you with your account.",
            customer_context={"customer_id": "C-1234", "account_tenure_days": 120},
        )
        assert result.pii_detected is True
        assert "pii-detection" in result.guardrails_applied
        assert "123-45-6789" not in result.corrected_text
        assert "[SSN REDACTED]" in result.corrected_text

    @pytest.mark.asyncio
    async def test_pii_plus_compliance(self):
        """PII redaction + policy correction should both apply."""
        result = await run_autocorrection(
            text="I'll give you a $200 credit. Your email is john@test.com.",
            customer_context={
                "customer_id": "C-1234",
                "account_tenure_days": 30,
            },
        )
        assert result.pii_detected is True
        assert "john@test.com" not in result.corrected_text

    @pytest.mark.asyncio
    async def test_no_pii_no_guardrails_flag(self):
        """Clean text should not trigger PII detection."""
        result = await run_autocorrection(
            text="I can help you with that question about your account.",
            customer_context={"customer_id": "C-1234", "account_tenure_days": 120},
        )
        assert result.pii_detected is False
        assert result.guardrails_applied == []

    @pytest.mark.asyncio
    async def test_credit_card_redacted_in_pipeline(self):
        result = await run_autocorrection(
            text="Your card 4532-1234-5678-9012 has been charged.",
            customer_context={"customer_id": "C-1234"},
        )
        assert result.pii_detected is True
        assert "[CARD REDACTED]" in result.corrected_text


class TestRedactionTokensRoundTrip:
    """Redaction is idempotent: a token never matches a pattern a second time."""

    @pytest.mark.parametrize("pii_type", list(validators._PII_PATTERNS))
    def test_token_survives_guard(self, pii_type):
        token = validators._PII_PATTERNS[pii_type][1]
        text = f"Your {token} is on file."
        assert build_pii_guard().validate(text).validated_output == text

    @pytest.mark.parametrize("pii_type", list(validators._PII_PATTERNS))
    def test_token_survives_span_mirror(self, pii_type):
        token = validators._PII_PATTERNS[pii_type][1]
        text = f"Your {token} is on file."
        assert find_pii_spans(text) == []
        assert redact_spans(text, find_pii_spans(text)) == text


class TestEmailPatternCost:
    """The email pattern is linear in the input (it was quadratic up to v1.5)."""

    def test_long_dotted_text_is_scanned_quickly(self):
        import time

        text = "a." * 20_000  # 40k chars with no "@": every position restarted the scan
        start = time.perf_counter()
        assert find_pii_spans(text) == []
        assert time.perf_counter() - start < 1.0  # about 5 ms; 3.7 s before the fix

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Reach me at john.doe@example.com today.", "john.doe@example.com"),
            ("Send it to a_b%c+tag-1@mail.sub-domain.co.uk.", "a_b%c+tag-1@mail.sub-domain.co.uk"),
            ("x@y.io", "x@y.io"),
            (f"{'a' * 64}@example.com", f"{'a' * 64}@example.com"),
            # Longer than a valid local part: still redacted whole, never suffix-only
            (f"{'a' * 65}@example.com", f"{'a' * 65}@example.com"),
            (f"jane.doe+{'x' * 70}@example.com", f"jane.doe+{'x' * 70}@example.com"),
            (f"{'ab.' * 40}c@example.com", f"{'ab.' * 40}c@example.com"),
            (f"a@{'sub.' * 70}example.com", f"a@{'sub.' * 70}example.com"),
            ("Write to .john@example.com.", ".john@example.com"),
            # Non-ASCII local parts: matched from the last ASCII run, as in v1.5
            ("josé.garcia@example.com", ".garcia@example.com"),
            ("straße+shop@example.de", "e+shop@example.de"),
        ],
    )
    def test_email_span_is_unchanged(self, text, expected):
        spans = find_pii_spans(text)
        assert [(t, text[s:e]) for t, s, e in spans] == [("email", expected)]
