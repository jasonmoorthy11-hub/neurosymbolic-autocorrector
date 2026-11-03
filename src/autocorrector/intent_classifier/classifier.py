"""Intent Classification Engine.

Phase 1 (MVP): Rule-based keyword/pattern matching for known intents.
Phase 2: Fine-tuned BERT/RoBERTa with LoRA for production scale.
Phase 3: LLM-based classification for edge cases.

Detects what type of action the AI agent is attempting:
- Financial compensation (credits, refunds, adjustments)
- Policy explanation (coverage, terms, limits)
- Escalation request (supervisor, manager, complaint)
- Refund processing (returns, cancellations)
- Product inquiry (features, pricing, availability)
- Troubleshooting (technical support, fixes)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from autocorrector.models import DetectedIntent, IntentType


@dataclass
class IntentPattern:
    """A pattern that maps keywords/regex to an intent type."""

    intent_type: IntentType
    keywords: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)  # regex patterns
    entity_extractors: dict[str, str] = field(default_factory=dict)  # entity_name -> regex
    base_confidence: float = 0.85


class IntentClassifier:
    """Rule-based intent classifier for the MVP.

    Uses keyword matching and regex patterns to classify agent outputs
    into intent categories. Returns the highest-confidence match.
    """

    def __init__(self) -> None:
        self._patterns = self._build_patterns()

    def classify(self, text: str) -> DetectedIntent:
        """Classify the intent of an agent's output text."""
        text_lower = text.lower()
        best_match: DetectedIntent | None = None
        best_score = 0.0

        for pattern in self._patterns:
            score, entities = self._score_pattern(text_lower, text, pattern)
            if score > best_score:
                best_score = score
                best_match = DetectedIntent(
                    intent_type=pattern.intent_type,
                    confidence=min(score, 1.0),
                    extracted_entities=entities,
                )

        if best_match and best_match.confidence >= 0.3:
            return best_match

        return DetectedIntent(
            intent_type=IntentType.GENERAL,
            confidence=0.5,
            extracted_entities={},
        )

    def _score_pattern(
        self, text_lower: str, text_original: str, pattern: IntentPattern
    ) -> tuple[float, dict]:
        """Score how well a text matches an intent pattern."""
        keyword_hits = 0
        total_keywords = len(pattern.keywords)

        for keyword in pattern.keywords:
            if keyword in text_lower:
                keyword_hits += 1

        if total_keywords == 0 and not pattern.patterns:
            return 0.0, {}

        # Keyword score
        keyword_score = (keyword_hits / total_keywords) if total_keywords > 0 else 0.0

        # Pattern (regex) score
        pattern_score = 0.0
        for regex_pattern in pattern.patterns:
            if re.search(regex_pattern, text_lower):
                pattern_score = 1.0
                break

        # Combined score
        if keyword_score > 0 and pattern_score > 0:
            score = pattern.base_confidence + 0.1  # bonus for both matching
        elif pattern_score > 0:
            score = pattern.base_confidence
        elif keyword_score > 0:
            score = keyword_score * pattern.base_confidence
        else:
            score = 0.0

        # Extract entities
        entities = {}
        for entity_name, entity_regex in pattern.entity_extractors.items():
            match = re.search(entity_regex, text_original)
            if match:
                entities[entity_name] = match.group(1)

        return score, entities

    @staticmethod
    def _build_patterns() -> list[IntentPattern]:
        """Build the intent pattern library."""
        return [
            IntentPattern(
                intent_type=IntentType.FINANCIAL_COMPENSATION,
                keywords=[
                    "credit",
                    "compensation",
                    "offer you",
                    "discount",
                    "adjustment",
                    "courtesy",
                    "goodwill",
                    "waive",
                    "free month",
                    "promo",
                    "promotional",
                ],
                patterns=[
                    r"\$\d+.*(?:credit|compensation|discount|adjustment)",
                    r"(?:offer|give|apply|provide).*\$\d+",
                    r"(?:credit|compensation|adjustment).*\$\d+",
                    r"waive.*(?:fee|charge)",
                    r"(?:offer|give|apply|provide).*\d+\s+(?:credit|dollar)",
                    r"(?:one|two|three|four|five|hundred|thousand)\s+(?:dollar|credit)",
                ],
                entity_extractors={
                    "amount": r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)",
                },
                base_confidence=0.90,
            ),
            IntentPattern(
                intent_type=IntentType.POLICY_EXPLANATION,
                keywords=[
                    "plan covers",
                    "coverage",
                    "policy",
                    "included",
                    "your plan",
                    "benefits",
                    "limit",
                    "deductible",
                    "copay",
                    "premium",
                    "terms",
                    "conditions",
                ],
                patterns=[
                    r"(?:your|the)\s+(?:plan|policy|coverage)\s+(?:covers|includes|provides)",
                    r"(?:up to|maximum|limit of)\s+\$\d+",
                    r"(?:deductible|copay|premium)\s+(?:is|of)\s+\$\d+",
                ],
                entity_extractors={
                    "coverage_amount": r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)",
                    "plan_type": r"(?:your|the)\s+(\w+)\s+plan",
                },
                base_confidence=0.85,
            ),
            IntentPattern(
                intent_type=IntentType.REFUND_PROCESSING,
                keywords=[
                    "refund",
                    "money back",
                    "return",
                    "cancel",
                    "cancellation",
                    "reverse",
                    "charge back",
                ],
                patterns=[
                    r"(?:process|issue|initiate).*refund",
                    r"refund.*\$\d+",
                    r"(?:cancel|reverse).*(?:order|charge|payment)",
                ],
                entity_extractors={
                    "refund_amount": r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)",
                },
                base_confidence=0.90,
            ),
            IntentPattern(
                intent_type=IntentType.ESCALATION_REQUEST,
                keywords=[
                    "supervisor",
                    "manager",
                    "escalate",
                    "complaint",
                    "speak to",
                    "transfer",
                    "higher authority",
                    "legal action",
                    "attorney",
                    "lawyer",
                    "lawsuit",
                    "senior team",
                    "legal department",
                ],
                patterns=[
                    r"(?:transfer|connect|escalate).*(?:supervisor|manager|senior)",
                    r"(?:let me|i'll).*(?:get|find|connect).*(?:supervisor|manager|someone|senior)",
                    r"(?:legal\s+action|attorney|lawyer|lawsuit|litigation)",
                ],
                entity_extractors={},
                base_confidence=0.90,
            ),
            IntentPattern(
                intent_type=IntentType.PRODUCT_INQUIRY,
                keywords=[
                    "product",
                    "feature",
                    "pricing",
                    "price",
                    "available",
                    "option",
                    "package",
                    "tier",
                    "upgrade",
                    "downgrade",
                    "offers",
                    "includes",
                ],
                patterns=[
                    r"(?:this|our|the)\s+\w+\s+(?:product|plan|package)\s+(?:offers|includes|features)",
                    r"(?:our|the)\s+(?:product|plan|package)\s+(?:offers|includes|features)",
                    r"(?:pricing|price|cost)\s+(?:is|starts|for)",
                    r"\w+\s+(?:package|plan)\s+offers",
                ],
                entity_extractors={
                    "product_name": r"(?:the|our)\s+(\w+(?:\s+\w+)?)\s+(?:plan|package|product)",
                },
                base_confidence=0.80,
            ),
            IntentPattern(
                intent_type=IntentType.TROUBLESHOOTING,
                keywords=[
                    "try",
                    "restart",
                    "reset",
                    "troubleshoot",
                    "fix",
                    "resolve",
                    "issue",
                    "problem",
                    "not working",
                    "error",
                    "steps",
                ],
                patterns=[
                    r"(?:try|please)\s+(?:restarting|resetting|clearing)",
                    r"(?:follow|here are|try)\s+(?:these|the)\s+steps",
                    r"(?:this|that)\s+should\s+(?:fix|resolve|help)",
                ],
                entity_extractors={},
                base_confidence=0.80,
            ),
            IntentPattern(
                intent_type=IntentType.COMPLAINT,
                keywords=[
                    "sorry to hear",
                    "apologize",
                    "understand your frustration",
                    "dissatisfied",
                    "unhappy",
                    "terrible experience",
                    "unacceptable",
                    "disappointed",
                    "complaint",
                    "formal complaint",
                ],
                patterns=[
                    r"(?:i'?m?\s+(?:so\s+)?sorry|i\s+apologize)\s+(?:to\s+hear|for|about|that)",
                    r"(?:file|lodge|submit|register)\s+(?:a\s+)?(?:formal\s+)?complaint",
                    r"(?:understand|hear)\s+(?:your|how)\s+(?:frustration|concern|disappointment)",
                ],
                entity_extractors={},
                base_confidence=0.85,
            ),
            IntentPattern(
                intent_type=IntentType.CANCELLATION,
                keywords=[
                    "cancel your",
                    "cancellation",
                    "terminate",
                    "discontinue",
                    "end your",
                    "close your account",
                    "opt out",
                    "unsubscribe",
                    "stop your",
                ],
                patterns=[
                    r"(?:cancel|terminate|discontinue|end)\s+(?:your|the|this)\s+(?:account|plan|service|subscription|membership)",
                    r"(?:close|deactivate)\s+(?:your|the|this)\s+account",
                    r"(?:process|initiate|submit)\s+(?:a\s+)?cancellation",
                ],
                entity_extractors={},
                base_confidence=0.90,
            ),
        ]
