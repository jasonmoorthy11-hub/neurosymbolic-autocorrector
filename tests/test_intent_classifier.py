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
