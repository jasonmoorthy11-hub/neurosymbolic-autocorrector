"""LangGraph Orchestration Pipeline for the Neurosymbolic Autocorrector.

Implements the core flow:
    agent_output → classify_intent → guardrails_check → validate → {allow | correct | escalate}

The pipeline routes agent outputs through intent classification, guardrails
safety checks, policy validation, fact checking, and correction — with
retry logic and escalation for critical violations.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from autocorrector.audit.store import AuditStore
from autocorrector.config import get_settings
from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.guardrails.validators import detect_pii_types
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.rewriter import fact_edits
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import (
    CorrectionAction,
    CorrectionEdit,
    CorrectionResult,
    CustomerContext,
    DetectedIntent,
    ExplainabilityReport,
    IntentType,
    Severity,
    ValidationResult,
)

logger = logging.getLogger(__name__)


def _max_correction_attempts() -> int:
    return get_settings().max_correction_attempts


_SEVERITY_ORDER = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


# --- Pipeline State ---


class PipelineState(TypedDict, total=False):
    """State that flows through the LangGraph pipeline.

    Using TypedDict with total=False so all keys are optional —
    LangGraph merges returned dicts into the accumulated state.
    """

    # Input (set at invocation)
    agent_text: str
    customer_context: dict[str, Any]
    agent_id: str | None
    session_id: str | None
    request_id: str | None
    start_time: float

    # Multilingual
    detected_language: str
    original_text_untranslated: str
    translation_failed: bool  # Non-English text that could not be translated

    # Intermediate (set by nodes)
    intent: DetectedIntent
    validation: ValidationResult
    initial_validation: ValidationResult  # First validation result (before any correction)
    corrected_text: str | None
    correction_attempts: int
    pii_detected: bool  # Whether PII was found and redacted
    pii_types: list[str]  # Which PII types were redacted, in pattern order
    redacted_text: str  # Agent text after PII redaction (set only when PII was found)
    edits: list[CorrectionEdit]  # Every change made by correct_node, in order
    guardrails_applied: list[str]  # Which guardrail validators fired
    nemo_blocked: bool  # Whether NeMo input rails blocked the input

    # Explainability
    explanation: ExplainabilityReport | None

    # Output (set by terminal nodes)
    final_text: str | None
    action_taken: CorrectionAction
    latency_ms: float


# --- Node Functions ---


async def detect_and_translate_node(state: PipelineState) -> dict[str, Any]:
    """Detect language and translate to English if needed."""
    from autocorrector.multilingual.detector import detect_language

    text = state["agent_text"]
    lang = detect_language(text)
    updates: dict[str, Any] = {
        "detected_language": lang,
        "original_text_untranslated": text,
    }

    if lang != "en":
        settings = get_settings()
        translated = None
        if lang in settings.supported_languages and settings.anthropic_api_key:
            from anthropic import AsyncAnthropic

            from autocorrector.multilingual import translator

            client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            result = await translator.translate_to_english_checked(
                text, lang, client, settings.default_llm
            )
            if result.ok:
                translated = result.text

        if translated is not None:
            updates["agent_text"] = translated
            logger.info(f"Translated input from {lang} to en")
        else:
            # English-only policy checks on untranslated text would silently pass it;
            # escalate after PII redaction instead (see route_after_validation).
            updates["translation_failed"] = True
            updates["guardrails_applied"] = ["translation-unavailable"]
            logger.warning(f"Translation from {lang} unavailable; output will be escalated")

    return updates


async def nemo_input_check_node(state: PipelineState) -> dict[str, Any]:
    """Run NeMo Guardrails input rails on the agent text.

    If NeMo is disabled or not installed, this is a no-op passthrough.
    If NeMo blocks the input, sets nemo_blocked=True for routing.
    """
    settings = get_settings()
    if not settings.nemo_rails_enabled:
        return {"nemo_blocked": False}

    from autocorrector.guardrails.nemo_rails import check_input

    text = state["agent_text"]
    result = await check_input(text, config_path=settings.nemo_config_path)

    if not result.allowed:
        logger.warning(f"NeMo input rail blocked: {result.rails_triggered} — {result.reason[:100]}")
        return {
            "nemo_blocked": True,
            "guardrails_applied": [*state.get("guardrails_applied", []), *result.rails_triggered],
        }

    return {"nemo_blocked": False}


def classify_intent_node(state: PipelineState) -> dict[str, Any]:
    """Classify the intent of the agent's output text."""
    classifier = _get_classifier()
    text = state["agent_text"]
    intent = classifier.classify(text)
    logger.info(f"Intent: {intent.intent_type.value} (confidence={intent.confidence:.2f})")
    return {"intent": intent}


def _redact_pii(state: PipelineState) -> dict[str, Any]:
    """Run the PII rail on the current text and return the state updates.

    Sets ``pii_detected``, ``pii_types`` and ``guardrails_applied``; when PII is
    found, ``corrected_text`` and ``redacted_text`` carry the redacted text.
    """
    text = state.get("corrected_text") or state["agent_text"]
    guardrails_applied: list[str] = list(state.get("guardrails_applied", []))

    # PII detection and redaction (Guardrails hub telemetry off unless opted in)
    pii_guard = build_pii_guard()
    pii_result = pii_guard.validate(text)

    pii_detected = False
    pii_types: list[str] = []
    if pii_result.validated_output and pii_result.validated_output != text:
        pii_detected = True
        pii_types = detect_pii_types(text)
        text = pii_result.validated_output
        guardrails_applied.append("pii-detection")
        logger.info("PII detected and redacted in agent output")

    updates: dict[str, Any] = {
        "pii_detected": pii_detected,
        "pii_types": pii_types,
        "guardrails_applied": guardrails_applied,
    }
    if pii_detected:
        # Replace the text going forward with the redacted version
        updates["corrected_text"] = text
        updates["redacted_text"] = text

    return updates


def guardrails_check_node(state: PipelineState) -> dict[str, Any]:
    """Run Guardrails AI safety validators on the text.

    Currently runs PII detection as an input safety rail.
    If PII is found, the text is redacted before continuing through
    the pipeline. This prevents PII from reaching the customer.
    """
    return _redact_pii(state)


async def validate_node(state: PipelineState) -> dict[str, Any]:
    """Validate the text against policy rules and facts."""
    kg = _get_kg_store()
    text = state.get("corrected_text") or state["agent_text"]
    intent = state["intent"]
    customer_ctx = CustomerContext(**state.get("customer_context", {}))

    # A check that raises cannot vouch for the reply: fail closed (escalate,
    # keep the human in the loop) rather than skip its rules and allow.
    validation_failed = False

    # Run policy compliance check
    try:
        violations = await kg.check_policy_compliance(
            text=text,
            intent_type=intent.intent_type.value,
            customer_context=customer_ctx.model_dump(exclude_none=True),
        )
    except Exception:
        logger.exception("Policy compliance check failed; escalating")
        violations = []
        validation_failed = True

    # Run fact verification
    try:
        fact_results = await kg.verify_facts(
            text=text,
            customer_context=customer_ctx.model_dump(exclude_none=True),
        )
        fact_errors = [f for f in fact_results if not f.is_accurate]
    except Exception:
        logger.exception("Fact verification failed; escalating")
        fact_errors = []
        validation_failed = True

    max_severity = Severity.LOW
    for v in violations:
        if _SEVERITY_ORDER.get(v.severity, 0) > _SEVERITY_ORDER.get(max_severity, 0):
            max_severity = v.severity

    # Determine action
    if validation_failed:
        validation = ValidationResult(
            is_compliant=False,
            action=CorrectionAction.ESCALATE,
            violations=violations,
            fact_errors=fact_errors,
            severity=max_severity,
        )
    elif not violations and not fact_errors:
        validation = ValidationResult(
            is_compliant=True,
            action=CorrectionAction.ALLOW,
            severity=Severity.LOW,
        )
    else:
        if max_severity == Severity.CRITICAL:
            action = CorrectionAction.ESCALATE
        elif violations or fact_errors:
            action = CorrectionAction.CORRECT
        else:
            action = CorrectionAction.WARN

        validation = ValidationResult(
            is_compliant=False,
            action=action,
            violations=violations,
            fact_errors=fact_errors,
            severity=max_severity,
        )

    logger.info(
        f"Validation: compliant={validation.is_compliant}, action={validation.action.value}"
    )
    updates: dict[str, Any] = {"validation": validation}
    if validation_failed:
        updates["guardrails_applied"] = [
            *state.get("guardrails_applied", []),
            "validation-unavailable",
        ]
    # Save the first validation as initial_validation (don't overwrite on revalidation)
    if "initial_validation" not in state:
        updates["initial_validation"] = validation
    return updates


async def correct_node(state: PipelineState) -> dict[str, Any]:
    """Generate a corrected version of the text."""
    kg = _get_kg_store()
    text = state.get("corrected_text") or state["agent_text"]
    validation = state["validation"]
    intent = state["intent"]
    customer_ctx = CustomerContext(**state.get("customer_context", {}))
    attempts = state.get("correction_attempts", 0)

    # Apply fact corrections at the claimed spans (the text validate_node checked)
    fact = fact_edits(text, validation.fact_errors)
    corrected = fact.text
    edits = [*state.get("edits", []), *fact.edits]

    # Fetch ACE strategies for this intent type
    strategies = None
    audit = _get_audit_store()
    if audit:
        try:
            strategies = await audit.get_top_strategies(
                intent_type=intent.intent_type.value, limit=10
            )
        except Exception:
            logger.debug("Could not fetch ACE strategies")

    # Apply policy corrections
    if validation.violations:
        try:
            rewritten = await kg.generate_compliant_alternative_with_edits(
                original_text=corrected,
                violations=[v.model_dump() for v in validation.violations],
                intent_type=intent.intent_type.value,
                customer_context=customer_ctx.model_dump(exclude_none=True),
                strategies=strategies,
            )
            corrected = rewritten.text
            edits = [*edits, *rewritten.edits]
        except Exception:
            logger.exception("Correction generation failed, delivering original text")

    logger.info(f"Correction attempt {attempts + 1}: modified={corrected != text}")

    # Build explainability report
    explanation = ExplainabilityEngine.build_report(
        original_text=text,
        corrected_text=corrected,
        intent=intent,
        violations=validation.violations,
        fact_errors=validation.fact_errors,
        confidence=get_settings().correction_confidence_score if corrected != text else 1.0,
    )

    return {
        "corrected_text": corrected,
        "correction_attempts": attempts + 1,
        "explanation": explanation,
        "edits": edits,
    }


async def allow_node(state: PipelineState) -> dict[str, Any]:
    """Allow the output through — it's compliant."""
    text = state.get("corrected_text") or state["agent_text"]
    text = await _translate_output_if_needed(text, state)
    elapsed = (time.monotonic() - state.get("start_time", 0)) * 1000
    return {
        "final_text": text,
        "action_taken": CorrectionAction.ALLOW,
        "latency_ms": elapsed,
    }


def escalate_node(state: PipelineState) -> dict[str, Any]:
    """Escalate to a supervisor — critical violation.

    An early exit (a NeMo block) reaches here before the PII rail ran; redact
    first so the escalation payload never carries raw PII.
    """
    updates: dict[str, Any] = {} if "pii_detected" in state else _redact_pii(state)
    elapsed = (time.monotonic() - state.get("start_time", 0)) * 1000
    return {
        **updates,
        "final_text": None,  # Do not send — needs supervisor review
        "action_taken": CorrectionAction.ESCALATE,
        "latency_ms": elapsed,
    }


async def deliver_correction_node(state: PipelineState) -> dict[str, Any]:
    """Deliver the corrected text."""
    text = state.get("corrected_text")
    if text:
        text = await _translate_output_if_needed(text, state)
    elapsed = (time.monotonic() - state.get("start_time", 0)) * 1000
    return {
        "final_text": text,
        "action_taken": CorrectionAction.CORRECT,
        "latency_ms": elapsed,
    }


async def _translate_output_if_needed(text: str, state: PipelineState) -> str:
    """Translate output back to detected language if non-English."""
    lang = state.get("detected_language", "en")
    if lang == "en":
        return text

    settings = get_settings()
    if lang not in settings.supported_languages:
        return text

    from anthropic import AsyncAnthropic

    from autocorrector.multilingual.translator import translate_from_english

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    translated = await translate_from_english(text, lang, client, settings.default_llm)
    logger.info(f"Translated output from en to {lang}")
    return translated


# --- Routing Functions ---


def route_after_nemo(
    state: PipelineState,
) -> Literal["classify_intent", "escalate"]:
    """Route after NeMo input check — escalate if blocked, else continue."""
    if state.get("nemo_blocked"):
        return "escalate"
    return "classify_intent"


def route_after_validation(
    state: PipelineState,
) -> Literal["allow", "correct", "escalate"]:
    """Route based on validation result."""
    validation = state["validation"]

    if state.get("translation_failed"):
        return "escalate"
    if validation.is_compliant:
        return "allow"
    if validation.action == CorrectionAction.ESCALATE:
        return "escalate"
    return "correct"


def route_after_correction(
    state: PipelineState,
) -> Literal["revalidate", "deliver"]:
    """Route after correction — revalidate or deliver."""
    attempts = state.get("correction_attempts", 0)
    max_attempts = _max_correction_attempts()
    if attempts >= max_attempts:
        logger.warning(f"Max correction attempts ({max_attempts}) reached, delivering as-is")
        return "deliver"
    return "revalidate"


# --- Shared Resources (module-level singletons for the pipeline) ---

_classifier: IntentClassifier | None = None
_kg_store: KnowledgeGraphStore | None = None
_audit_store: AuditStore | None = None
_lock = threading.Lock()


def _get_classifier() -> IntentClassifier:
    global _classifier
    if _classifier is None:
        with _lock:
            if _classifier is None:
                _classifier = IntentClassifier()
    return _classifier


def _get_kg_store() -> KnowledgeGraphStore:
    global _kg_store
    if _kg_store is None:
        with _lock:
            if _kg_store is None:
                settings = get_settings()
                _kg_store = KnowledgeGraphStore(
                    anthropic_api_key=settings.anthropic_api_key,
                    correction_llm=settings.correction_llm,
                )
    return _kg_store


def set_kg_store(store: KnowledgeGraphStore) -> None:
    """Inject a KG store (e.g., from lifespan context)."""
    global _kg_store
    with _lock:
        _kg_store = store


def set_classifier(classifier: IntentClassifier) -> None:
    """Inject an intent classifier."""
    global _classifier
    with _lock:
        _classifier = classifier


def _get_audit_store() -> AuditStore | None:
    return _audit_store


def set_audit_store(store: AuditStore) -> None:
    """Inject an audit store (for ACE strategy fetching during corrections)."""
    global _audit_store
    with _lock:
        _audit_store = store


# --- Build the Graph ---


def build_pipeline() -> StateGraph:
    """Build and compile the autocorrection LangGraph pipeline.

    Graph structure:
        START → classify_intent → validate → {allow | correct | escalate}
                                                   ↓
                                              correct → revalidate → {allow | correct | escalate}
                                                   ↓ (max attempts)
                                                deliver
    """
    graph = StateGraph(PipelineState)

    # Add nodes
    graph.add_node("detect_and_translate", detect_and_translate_node)
    graph.add_node("nemo_input_check", nemo_input_check_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("guardrails_check", guardrails_check_node)
    graph.add_node("validate", validate_node)
    graph.add_node("correct", correct_node)
    graph.add_node("allow", allow_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("deliver", deliver_correction_node)

    # Wire edges — language detection is the first node
    graph.add_edge(START, "detect_and_translate")
    graph.add_edge("detect_and_translate", "nemo_input_check")
    graph.add_conditional_edges(
        "nemo_input_check",
        route_after_nemo,
        {"classify_intent": "classify_intent", "escalate": "escalate"},
    )
    graph.add_edge("classify_intent", "guardrails_check")
    graph.add_edge("guardrails_check", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {"allow": "allow", "correct": "correct", "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "correct",
        route_after_correction,
        {"revalidate": "validate", "deliver": "deliver"},
    )
    graph.add_edge("allow", END)
    graph.add_edge("escalate", END)
    graph.add_edge("deliver", END)

    return graph.compile()


# --- Convenience runner ---


async def run_autocorrection(
    text: str,
    customer_context: dict[str, Any] | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    kg_store: KnowledgeGraphStore | None = None,
    classifier: IntentClassifier | None = None,
    request_id: str | None = None,
    audit_store: AuditStore | None = None,
) -> CorrectionResult:
    """Run the full autocorrection pipeline on agent text.

    This is the main entry point for using the pipeline programmatically.
    """
    if kg_store:
        set_kg_store(kg_store)
    if classifier:
        set_classifier(classifier)
    if audit_store:
        set_audit_store(audit_store)

    # Set correlation ID so all node logs share the same request_id
    if request_id:
        from autocorrector.logging_config import request_id_var

        request_id_var.set(request_id)

    # Ensure KG store is connected
    store = _get_kg_store()
    if not store.is_connected and not store.is_configured:
        await store.connect()

    pipeline = build_pipeline()

    initial_state: PipelineState = {
        "agent_text": text,
        "customer_context": customer_context or {},
        "agent_id": agent_id,
        "session_id": session_id,
        "request_id": request_id,
        "start_time": time.monotonic(),
        "correction_attempts": 0,
    }

    # Run the pipeline
    result = await pipeline.ainvoke(initial_state)

    # Build CorrectionResult
    intent = result.get(
        "intent",
        DetectedIntent(intent_type=IntentType.GENERAL, confidence=0.0),
    )
    # Use initial_validation to reflect what was originally found
    default_validation = ValidationResult(
        is_compliant=True,
        action=CorrectionAction.ALLOW,
        severity=Severity.LOW,
    )
    initial_validation = result.get("initial_validation") or result.get(
        "validation", default_validation
    )
    final_text = result.get("final_text", text)
    # Escalated (final_text None): nothing is sent, but never hand back unredacted PII
    corrected_text = final_text or result.get("redacted_text") or text

    was_modified = corrected_text != text

    # action_taken reflects what the pipeline DID, not just the terminal node
    if result.get("action_taken") == CorrectionAction.ESCALATE:
        action = CorrectionAction.ESCALATE
    elif was_modified:
        # Text was changed (by PII redaction, policy correction, or fact fix)
        action = CorrectionAction.CORRECT
    else:
        action = result.get("action_taken", CorrectionAction.ALLOW)

    # Track if PII was detected
    pii_detected = result.get("pii_detected", False)
    guardrails_applied = result.get("guardrails_applied", [])
    # An escalation sends nothing, so no rewrite reaches the customer
    edits = [] if action == CorrectionAction.ESCALATE else result.get("edits", [])

    explanation = result.get("explanation")

    detected_language = result.get("detected_language", "en")

    return CorrectionResult(
        original_text=text,
        corrected_text=corrected_text,
        was_modified=was_modified,
        action_taken=action,
        confidence=get_settings().correction_confidence_score if was_modified else 1.0,
        intent=intent,
        validation=initial_validation,
        explanation=explanation,
        pii_detected=pii_detected,
        pii_types=result.get("pii_types", []),
        guardrails_applied=guardrails_applied,
        detected_language=detected_language,
        edits=edits,
    )
