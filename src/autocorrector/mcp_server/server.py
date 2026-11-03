"""Neurosymbolic Autocorrection MCP Server.

The core of the system — exposes autocorrection capabilities as MCP tools
that any AI agent can call to validate and correct its outputs before
delivering them to end users.

Tools:
    validate_action  - Check an agent's proposed action/output against policy rules
    get_correction   - Get a corrected version of non-compliant output
    log_correction_event - Record a correction event for audit trail

Resources:
    rules://compliance/{domain}   - Active compliance rules per domain
    metrics://corrections/summary - Correction statistics
    policies://active             - Currently loaded policy documents
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from autocorrector.audit.store import AuditStore
from autocorrector.config import get_settings
from autocorrector.explainability.engine import ExplainabilityEngine
from autocorrector.intent_classifier.classifier import IntentClassifier
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    CustomerContext,
    DetectedIntent,
    FactCheckResult,
    IntentType,
    PolicyViolation,
    Severity,
    ValidationResult,
)
from autocorrector.multilingual.detector import detect_language

logger = logging.getLogger(__name__)


# --- Lifespan: manage shared resources ---


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Initialize and teardown shared resources (KG, classifiers, etc.)."""
    logger.info("Starting Neurosymbolic Autocorrector...")

    settings = get_settings()
    kg_store = KnowledgeGraphStore(
        anthropic_api_key=settings.anthropic_api_key,
        correction_llm=settings.correction_llm,
    )
    intent_classifier = IntentClassifier()
    audit_store = AuditStore(db_path=settings.audit_db_path)

    try:
        await kg_store.connect()
        await audit_store.connect()
        logger.info("Knowledge graph and audit store connected")
        yield {
            "kg": kg_store,
            "classifier": intent_classifier,
            "audit_store": audit_store,
        }
    finally:
        await audit_store.close()
        await kg_store.close()
        logger.info("Autocorrector shut down")


# --- MCP Server ---


mcp = FastMCP(
    "Neurosymbolic Autocorrector",
    lifespan=app_lifespan,
    json_response=True,
    stateless_http=True,
)


# --- Helper functions ---


def _get_resources(ctx: Context) -> tuple[KnowledgeGraphStore, IntentClassifier, AuditStore]:
    lifespan = ctx.request_context.lifespan_context
    return lifespan["kg"], lifespan["classifier"], lifespan["audit_store"]


async def _run_validation(
    text: str,
    intent: DetectedIntent,
    customer_ctx: CustomerContext,
    kg: KnowledgeGraphStore,
) -> ValidationResult:
    """Run the full validation pipeline: policy check + fact check."""
    violations: list[PolicyViolation] = []
    fact_errors: list[FactCheckResult] = []

    # Policy rules are English-only and these tools do not translate, so
    # non-English text is escalated rather than silently allowed.
    if detect_language(text) != "en":
        return ValidationResult(
            is_compliant=False,
            action=CorrectionAction.ESCALATE,
            severity=Severity.HIGH,
        )

    # 1. Policy compliance check via knowledge graph
    policy_results = await kg.check_policy_compliance(
        text=text,
        intent_type=intent.intent_type.value,
        customer_context=customer_ctx.model_dump(exclude_none=True),
    )
    violations.extend(policy_results)

    # 2. Fact verification against knowledge graph
    fact_results = await kg.verify_facts(
        text=text,
        customer_context=customer_ctx.model_dump(exclude_none=True),
    )
    fact_errors.extend([f for f in fact_results if not f.is_accurate])

    # Determine action based on violations
    if not violations and not fact_errors:
        return ValidationResult(
            is_compliant=True,
            action=CorrectionAction.ALLOW,
            severity=Severity.LOW,
        )

    severity_order = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}
    max_severity = Severity.LOW
    for v in violations:
        if severity_order.get(v.severity, 0) > severity_order.get(max_severity, 0):
            max_severity = v.severity

    if max_severity == Severity.CRITICAL:
        action = CorrectionAction.ESCALATE
    elif violations or fact_errors:
        action = CorrectionAction.CORRECT
    else:
        action = CorrectionAction.WARN

    return ValidationResult(
        is_compliant=False,
        action=action,
        violations=violations,
        fact_errors=fact_errors,
        severity=max_severity,
    )


async def _generate_correction(
    text: str,
    validation: ValidationResult,
    intent: DetectedIntent,
    customer_ctx: CustomerContext,
    kg: KnowledgeGraphStore,
) -> str:
    """Generate a corrected version of the text using KG facts."""
    if validation.is_compliant:
        return text

    corrected = text

    # Apply fact corrections (deterministic — replace wrong values with KG values)
    for fact_error in validation.fact_errors:
        if fact_error.correct_value and fact_error.claim:
            corrected = corrected.replace(fact_error.claim, fact_error.correct_value)

    # Apply policy corrections via KG-grounded rewriting
    if validation.violations:
        corrected = await kg.generate_compliant_alternative(
            original_text=corrected,
            violations=[v.model_dump() for v in validation.violations],
            intent_type=intent.intent_type.value,
            customer_context=customer_ctx.model_dump(exclude_none=True),
        )

    return corrected


# --- MCP Tools ---


@mcp.tool()
async def validate_action(
    text: str,
    action_type: str | None = None,
    customer_context: dict[str, Any] | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Validate an AI agent's proposed output against policy rules and factual accuracy.

    Checks the agent's text against the enterprise knowledge graph for:
    - Policy compliance (authorization levels, spending limits, procedures)
    - Factual accuracy (plan details, pricing, coverage amounts)
    - Intent-appropriate behavior (escalation protocols, disclosure requirements)

    Returns validation result with action recommendation: ALLOW, WARN, CORRECT, or ESCALATE.
    """
    kg, classifier, _ = _get_resources(ctx)

    if ctx:
        await ctx.info(f"Validating agent output ({len(text)} chars)")

    # 1. Classify intent
    if action_type:
        try:
            intent_type = IntentType(action_type)
        except ValueError:
            intent_type = IntentType.GENERAL
        intent = DetectedIntent(intent_type=intent_type, confidence=1.0, extracted_entities={})
    else:
        intent = classifier.classify(text)

    if ctx:
        await ctx.info(f"Intent: {intent.intent_type.value} (confidence: {intent.confidence:.2f})")

    # 2. Build customer context
    customer_ctx = CustomerContext(**(customer_context or {}))

    # 3. Validate
    validation = await _run_validation(text, intent, customer_ctx, kg)
    language = detect_language(text)

    if ctx:
        if language != "en":
            await ctx.warning(f"Language '{language}': escalate (translation-unavailable)")
        elif validation.is_compliant:
            await ctx.info("Output is compliant — ALLOW")
        else:
            await ctx.warning(
                f"Found {len(validation.violations)} violations, "
                f"{len(validation.fact_errors)} fact errors — {validation.action.value}"
            )

    return {
        "is_compliant": validation.is_compliant,
        "action": validation.action.value,
        "severity": validation.severity.value,
        "violations": [v.model_dump() for v in validation.violations],
        "fact_errors": [f.model_dump() for f in validation.fact_errors],
        "intent": {
            "type": intent.intent_type.value,
            "confidence": intent.confidence,
        },
        "detected_language": language,
        "reason": "translation-unavailable" if language != "en" else None,
    }


@mcp.tool()
async def get_correction(
    text: str,
    violations: list[dict[str, Any]] | None = None,
    fact_errors: list[dict[str, Any]] | None = None,
    customer_context: dict[str, Any] | None = None,
    intent_type: str = "general",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get a corrected version of non-compliant agent output.

    Takes the original text along with identified violations and factual errors,
    and generates a policy-compliant, factually accurate alternative that
    preserves the conversational tone and intent.

    Returns the corrected text with an explainability report.
    """
    kg, classifier, _ = _get_resources(ctx)

    if ctx:
        await ctx.info("Generating correction...")

    # Build typed models from input
    try:
        it = IntentType(intent_type)
    except ValueError:
        it = IntentType.GENERAL

    intent = DetectedIntent(intent_type=it, confidence=1.0)
    customer_ctx = CustomerContext(**(customer_context or {}))

    typed_violations = [PolicyViolation(**v) for v in (violations or [])]
    typed_fact_errors = [FactCheckResult(**f) for f in (fact_errors or [])]

    validation = ValidationResult(
        is_compliant=False,
        action=CorrectionAction.CORRECT,
        violations=typed_violations,
        fact_errors=typed_fact_errors,
        severity=Severity.HIGH if typed_violations else Severity.MEDIUM,
    )

    # Generate correction
    corrected_text = await _generate_correction(text, validation, intent, customer_ctx, kg)
    was_modified = corrected_text != text

    # Build explainability report
    explanation = ExplainabilityEngine.build_report(
        original_text=text,
        corrected_text=corrected_text,
        intent=intent,
        violations=typed_violations,
        fact_errors=typed_fact_errors,
        confidence=get_settings().correction_confidence_score if was_modified else 1.0,
    )

    if ctx:
        if was_modified:
            await ctx.info(f"Correction applied (id: {explanation.correction_id})")
        else:
            await ctx.info("No modification needed")

    return {
        "original_text": text,
        "corrected_text": corrected_text,
        "was_modified": was_modified,
        "confidence": explanation.confidence,
        "explanation": explanation.model_dump(mode="json"),
    }


@mcp.tool()
async def log_correction_event(
    original_text: str,
    corrected_text: str,
    was_modified: bool,
    action_taken: str,
    intent_type: str = "general",
    violations: list[dict[str, Any]] | None = None,
    confidence: float = 1.0,
    agent_id: str | None = None,
    session_id: str | None = None,
    latency_ms: float | None = None,
    ctx: Context | None = None,
) -> dict[str, str]:
    """Log a correction event to the audit trail.

    Records every validation and correction action for compliance auditing,
    supervisor review, and system performance monitoring.
    """
    _, _, audit_store = _get_resources(ctx)

    try:
        action = CorrectionAction(action_taken)
    except ValueError:
        action = CorrectionAction.ALLOW

    try:
        it = IntentType(intent_type)
    except ValueError:
        it = IntentType.GENERAL

    event = CorrectionEvent(
        event_id=str(uuid.uuid4()),
        agent_id=agent_id,
        session_id=session_id,
        original_text=original_text,
        corrected_text=corrected_text,
        was_modified=was_modified,
        action_taken=action,
        intent=DetectedIntent(intent_type=it, confidence=confidence),
        violations=[PolicyViolation(**v) for v in (violations or [])],
        confidence=confidence,
        latency_ms=latency_ms,
    )

    await audit_store.log_event(event)
    logger.info(f"Correction event logged: {event.event_id} (action={action.value})")

    if ctx:
        await ctx.info(f"Event {event.event_id} logged to audit trail")

    return {"event_id": event.event_id, "status": "logged"}


# --- MCP Resources ---


@mcp.resource("rules://compliance/{domain}")
async def get_compliance_rules(domain: str, ctx: Context | None = None) -> str:
    """Get active compliance rules for a specific domain from KG store."""
    import json

    # Hardcoded fallback for known domains
    _fallback_rules = {
        "financial_compensation": [
            {
                "id": "CREDIT-001",
                "name": "Credit Authorization Threshold",
                "description": "Credits >$50 require 90+ days account tenure",
                "severity": "high",
            },
            {
                "id": "CREDIT-002",
                "name": "Maximum Credit Amount",
                "description": "Single credit cannot exceed $500 without supervisor approval",
                "severity": "critical",
            },
        ],
        "policy_explanation": [
            {
                "id": "FACT-001",
                "name": "Coverage Accuracy",
                "description": "All coverage amounts must match the customer's actual plan",
                "severity": "high",
            },
            {
                "id": "FACT-002",
                "name": "Plan Feature Verification",
                "description": "Plan features must be verified against current product catalog",
                "severity": "medium",
            },
        ],
    }

    rules = []
    if ctx:
        kg: KnowledgeGraphStore = ctx.request_context.lifespan_context["kg"]
        rules = await kg.get_rules_by_domain(domain)

    # Fallback to hardcoded rules if KG returned empty and domain is known
    if not rules:
        rules = _fallback_rules.get(domain, [])

    return json.dumps({"domain": domain, "rules": rules}, indent=2)


@mcp.resource("metrics://corrections/summary")
async def get_correction_metrics(ctx: Context | None = None) -> str:
    """Get correction statistics summary from audit store."""
    import json

    if ctx:
        lifespan = ctx.request_context.lifespan_context
        audit_store: AuditStore = lifespan["audit_store"]
        metrics = await audit_store.get_metrics()
        return json.dumps(metrics, indent=2)

    # Fallback when called without context (e.g., tests)
    return json.dumps(
        {
            "total_validations": 0,
            "total_corrections": 0,
            "corrections_by_type": {},
            "average_confidence": 0.0,
            "average_latency_ms": 0.0,
        },
        indent=2,
    )


@mcp.resource("policies://active")
def get_active_policies() -> str:
    """List currently loaded policy documents."""
    import json

    return json.dumps(
        {
            "policies": [],
            "last_updated": datetime.now(UTC).isoformat(),
            "total_rules": 0,
            "total_facts": 0,
        },
        indent=2,
    )


# --- Prompts ---


@mcp.prompt()
def review_correction(original: str, corrected: str, explanation: str) -> str:
    """Template for supervisor review of a correction."""
    return f"""Please review this autocorrection:

ORIGINAL OUTPUT:
{original}

CORRECTED OUTPUT:
{corrected}

EXPLANATION:
{explanation}

Questions to consider:
1. Is the correction appropriate and accurate?
2. Does it preserve the conversational tone?
3. Should any rules be adjusted based on this case?"""


# --- Authentication Middleware ---


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """API key authentication middleware for the MCP HTTP transport.

    Checks for a valid API key in Authorization: Bearer <key> or X-API-Key header.
    Only applies to requests to the /mcp path. If mcp_api_key is empty (dev mode),
    authentication is disabled.
    """

    def __init__(self, app, api_key: str = ""):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # Skip auth if no key configured (dev mode)
        if not self.api_key:
            return await call_next(request)

        # Only authenticate /mcp path
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)

        # Check Authorization: Bearer <key>
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token == self.api_key:
                return await call_next(request)

        # Check X-API-Key header
        api_key_header = request.headers.get("x-api-key", "")
        if api_key_header == self.api_key:
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )


# --- Entry point ---


def main():
    """Run the MCP server."""
    import sys

    from autocorrector.logging_config import setup_logging

    setup_logging()

    transport = "streamable-http"
    if "--stdio" in sys.argv:
        transport = "stdio"

    settings = get_settings()

    logger.info(f"Starting MCP server (transport={transport})")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # Apply auth middleware for HTTP transport
        if settings.mcp_api_key:
            logger.info("MCP authentication enabled")

        starlette_app = mcp.streamable_http_app()
        starlette_app.add_middleware(MCPAuthMiddleware, api_key=settings.mcp_api_key)

        import uvicorn

        uvicorn.run(starlette_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
