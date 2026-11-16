"""The deterministic path as one plain coroutine, without LangGraph.

``run_deterministic`` gives the same ``CorrectionResult`` as
``pipeline.run_autocorrection`` does offline (no API key, no Neo4j, NeMo off, no
audit store): language check, intent, PII redaction, policy and fact checks, the
span and template correction tiers, revalidation, escalation. It imports only
pydantic and cachetools besides the package, so it also runs where LangGraph,
Guardrails AI and pydantic-settings do not, such as Pyodide in a browser.

``tests/test_runner_parity.py`` holds the two paths together on every golden
case and every replay scenario. A change to a node in ``pipeline.py`` needs the
same change here; that test fails until it is made.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.guardrails.pii import redact_pii
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.rewriter import fact_edits
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import (
    CorrectionAction,
    CorrectionEdit,
    CorrectionResult,
    CustomerContext,
    ExplainabilityReport,
    Severity,
    ValidationResult,
)
from autocorrector.multilingual.detector import UNDETERMINED, detect_language

logger = logging.getLogger(__name__)

# The defaults of ``Settings.max_correction_attempts`` and
# ``Settings.correction_confidence_score``; the parity test compares them.
MAX_CORRECTION_ATTEMPTS = 3
CORRECTION_CONFIDENCE = 0.92

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


def is_latin_script(text: str) -> bool:
    """Whether every letter in ``text`` is a Latin one (accents included)."""
    return all(unicodedata.name(char, "").startswith("LATIN") for char in text if char.isalpha())


def _language(text: str, assume_english: bool) -> str:
    if not assume_english:
        return detect_language(text)
    # The caller says the reply is English (the browser panel, which has no
    # language model). Another script is plainly not English, so it is held.
    return "en" if is_latin_script(text) else UNDETERMINED


async def _validate(
    kg: KnowledgeGraphStore, text: str, intent_type: str, context: dict[str, Any]
) -> tuple[ValidationResult, bool]:
    """The validation of ``text`` and whether a check raised (fail closed)."""
    failed = False
    try:
        violations = await kg.check_policy_compliance(
            text=text, intent_type=intent_type, customer_context=context
        )
    except Exception:
        logger.exception("Policy compliance check failed; escalating")
        violations, failed = [], True
    try:
        fact_results = await kg.verify_facts(text=text, customer_context=context)
        fact_errors = [f for f in fact_results if not f.is_accurate]
    except Exception:
        logger.exception("Fact verification failed; escalating")
        fact_errors, failed = [], True

    max_severity = Severity.LOW
    for violation in violations:
        if _SEVERITY_ORDER.get(violation.severity, 0) > _SEVERITY_ORDER.get(max_severity, 0):
            max_severity = violation.severity

    if failed:
        action = CorrectionAction.ESCALATE
    elif not violations and not fact_errors:
        return (
            ValidationResult(
                is_compliant=True, action=CorrectionAction.ALLOW, severity=Severity.LOW
            ),
            False,
        )
    elif max_severity == Severity.CRITICAL:
        action = CorrectionAction.ESCALATE
    else:
        action = CorrectionAction.CORRECT
    return (
        ValidationResult(
            is_compliant=False,
            action=action,
            violations=violations,
            fact_errors=fact_errors,
            severity=max_severity,
        ),
        failed,
    )


async def run_deterministic(
    text: str,
    customer_context: dict[str, Any] | None = None,
    *,
    kg_store: KnowledgeGraphStore | None = None,
    classifier: IntentClassifier | None = None,
    assume_english: bool = False,
    max_attempts: int = MAX_CORRECTION_ATTEMPTS,
    confidence: float = CORRECTION_CONFIDENCE,
) -> CorrectionResult:
    """Check and correct one agent reply on the deterministic path.

    ``assume_english`` skips language detection for a caller that only accepts
    English; text in another script is still held as undetermined.
    """
    kg = kg_store or KnowledgeGraphStore()
    intent_classifier = classifier or IntentClassifier()
    context = CustomerContext(**(customer_context or {})).model_dump(exclude_none=True)
    guardrails_applied: list[str] = []

    # Language: nothing translates on this path, so a foreign reply is held
    language = _language(text, assume_english)
    untranslated = language != "en"
    if untranslated:
        guardrails_applied.append("translation-unavailable")
        logger.warning(f"Translation from {language} unavailable; output will be escalated")

    intent = intent_classifier.classify(text)

    # PII rail: the redacted text is what every later step reads
    redacted, pii_types = redact_pii(text)
    pii_detected = redacted != text
    if pii_detected:
        guardrails_applied.append("pii-detection")
    else:
        pii_types = []
    corrected: str | None = redacted if pii_detected else None

    initial_validation: ValidationResult | None = None
    explanation: ExplainabilityReport | None = None
    edits: list[CorrectionEdit] = []
    attempts = 0
    final_text: str | None
    while True:
        current = corrected or text
        validation, failed = await _validate(kg, current, intent.intent_type.value, context)
        if failed:
            guardrails_applied.append("validation-unavailable")
        if initial_validation is None:
            initial_validation = validation

        if untranslated or (
            not validation.is_compliant and validation.action == CorrectionAction.ESCALATE
        ):
            final_text, node_action = None, CorrectionAction.ESCALATE
            break
        if validation.is_compliant:
            final_text, node_action = current, CorrectionAction.ALLOW
            break

        # Correct: facts at their spans first, then the policy tiers
        fact = fact_edits(current, validation.fact_errors)
        rewritten_text = fact.text
        edits = [*edits, *fact.edits]
        if validation.violations:
            try:
                rewritten = await kg.generate_compliant_alternative_with_edits(
                    original_text=rewritten_text,
                    violations=[v.model_dump() for v in validation.violations],
                    intent_type=intent.intent_type.value,
                    customer_context=context,
                    strategies=None,
                )
                rewritten_text = rewritten.text
                edits = [*edits, *rewritten.edits]
            except Exception:
                logger.exception("Correction generation failed, delivering original text")
        explanation = ExplainabilityEngine.build_report(
            original_text=current,
            corrected_text=rewritten_text,
            intent=intent,
            violations=validation.violations,
            fact_errors=validation.fact_errors,
            confidence=confidence if rewritten_text != current else 1.0,
        )
        corrected = rewritten_text
        attempts += 1
        if attempts >= max_attempts:
            logger.warning(f"Max correction attempts ({max_attempts}) reached, delivering as-is")
            final_text, node_action = corrected or None, CorrectionAction.CORRECT
            break

    # Escalated: nothing is sent, but never hand back unredacted PII
    result_text = final_text or (redacted if pii_detected else None) or text
    was_modified = result_text != text
    if node_action == CorrectionAction.ESCALATE:
        action = CorrectionAction.ESCALATE
    elif was_modified:
        action = CorrectionAction.CORRECT
    else:
        action = node_action

    return CorrectionResult(
        original_text=text,
        corrected_text=result_text,
        was_modified=was_modified,
        action_taken=action,
        confidence=confidence if was_modified else 1.0,
        intent=intent,
        validation=initial_validation,
        explanation=explanation,
        pii_detected=pii_detected,
        pii_types=pii_types,
        guardrails_applied=guardrails_applied,
        detected_language=language,
        edits=[] if action == CorrectionAction.ESCALATE else edits,
    )
