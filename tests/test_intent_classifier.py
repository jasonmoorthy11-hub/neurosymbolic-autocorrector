"""Tests for the intent classification engine."""

from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.models import IntentType


def test_financial_compensation_credit_offer():
    classifier = IntentClassifier()
    result = classifier.classify("I can offer you a $200 credit for the inconvenience.")
    assert result.intent_type == IntentType.FINANCIAL_COMPENSATION
    assert result.confidence >= 0.8
    assert "amount" in result.extracted_entities
    assert result.extracted_entities["amount"] == "200"


def test_financial_compensation_discount():
    classifier = IntentClassifier()
    result = classifier.classify(
        "Let me apply a $50 discount to your account as a goodwill gesture."
    )
    assert result.intent_type == IntentType.FINANCIAL_COMPENSATION
    assert result.confidence >= 0.8


def test_policy_explanation_coverage():
    classifier = IntentClassifier()
    result = classifier.classify("Your plan covers up to $5,000 in emergency dental work.")
    assert result.intent_type == IntentType.POLICY_EXPLANATION
    assert result.confidence >= 0.7


def test_policy_explanation_benefits():
    classifier = IntentClassifier()
    result = classifier.classify(
        "The Standard plan includes coverage for emergency room visits up to $10,000."
    )
    assert result.intent_type == IntentType.POLICY_EXPLANATION


def test_refund_processing():
    classifier = IntentClassifier()
    result = classifier.classify("I'll process a refund of $150 to your original payment method.")
    assert result.intent_type == IntentType.REFUND_PROCESSING
    assert result.confidence >= 0.8


def test_escalation_request():
    classifier = IntentClassifier()
    result = classifier.classify("Let me transfer you to a supervisor who can help with this.")
    assert result.intent_type == IntentType.ESCALATION_REQUEST
    assert result.confidence >= 0.8


def test_product_inquiry():
    classifier = IntentClassifier()
    result = classifier.classify("Our Premium package offers unlimited data and priority support.")
    assert result.intent_type == IntentType.PRODUCT_INQUIRY


def test_troubleshooting():
    classifier = IntentClassifier()
    result = classifier.classify("Please try restarting your router. Here are the steps to follow.")
    assert result.intent_type == IntentType.TROUBLESHOOTING


def test_general_fallback():
    classifier = IntentClassifier()
    result = classifier.classify("Hello, how can I help you today?")
    assert result.intent_type == IntentType.GENERAL


def test_entity_extraction_amount():
    classifier = IntentClassifier()
    result = classifier.classify("I can offer you a $350 credit for the billing error.")
    assert result.extracted_entities.get("amount") == "350"


def test_entity_extraction_large_amount():
    classifier = IntentClassifier()
    result = classifier.classify("We'll apply a $1,500 compensation credit to your account.")
    assert result.extracted_entities.get("amount") == "1,500"


# --- keyword scoring: any hit scores at base confidence (v1.2) ---


def test_single_refund_keyword_scores_base_confidence():
    classifier = IntentClassifier()
    result = classifier.classify(
        "Your money back will arrive on the card you used within five business days."
    )
    assert result.intent_type == IntentType.REFUND_PROCESSING
    assert result.confidence >= 0.85


def test_single_credit_keyword_scores_base_confidence():
    classifier = IntentClassifier()
    result = classifier.classify("We will add a credit to your next statement.")
    assert result.intent_type == IntentType.FINANCIAL_COMPENSATION
    assert result.confidence >= 0.85


def test_no_keyword_text_is_general():
    classifier = IntentClassifier()
    result = classifier.classify("Thanks for calling, have a great day.")
    assert result.intent_type == IntentType.GENERAL
    assert result.confidence == 0.5


def test_regex_plus_keyword_outranks_single_keyword():
    classifier = IntentClassifier()
    result = classifier.classify("Let me transfer you to a supervisor about the refund.")
    assert result.intent_type == IntentType.ESCALATION_REQUEST
    assert result.confidence >= 0.95


def test_more_keyword_hits_win_a_tie():
    classifier = IntentClassifier()
    result = classifier.classify("The refund puts your money back as a credit on the card.")
    assert result.intent_type == IntentType.REFUND_PROCESSING


def test_both_match_tie_keeps_pattern_order():
    classifier = IntentClassifier()
    result = classifier.classify("I'll process a refund and apply a $20 credit.")
    assert result.intent_type == IntentType.FINANCIAL_COMPENSATION
    assert result.confidence == 1.0


def test_money_transfer_is_not_an_escalation():
    classifier = IntentClassifier()
    result = classifier.classify("Please confirm the routing number for the wire transfer.")
    assert result.intent_type != IntentType.ESCALATION_REQUEST


def test_transfer_you_is_an_escalation():
    classifier = IntentClassifier()
    result = classifier.classify("I'll transfer you to our billing department now.")
    assert result.intent_type == IntentType.ESCALATION_REQUEST


def test_transfer_your_balance_is_not_an_escalation():
    classifier = IntentClassifier()
    result = classifier.classify("Let me transfer your balance to the new account.")
    assert result.intent_type != IntentType.ESCALATION_REQUEST


def test_keywords_match_at_word_starts_only():
    classifier = IntentClassifier()
    result = classifier.classify("We ship to every country in the region.")
    assert result.intent_type == IntentType.GENERAL


def test_keyword_inflections_still_match():
    classifier = IntentClassifier()
    result = classifier.classify("Your refunds were sent back to the original card.")
    assert result.intent_type == IntentType.REFUND_PROCESSING
