"""Shared data models for the autocorrection pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CorrectionAction(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    CORRECT = "correct"
    BLOCK = "block"
    ESCALATE = "escalate"


class IntentType(StrEnum):
    FINANCIAL_COMPENSATION = "financial_compensation"
    POLICY_EXPLANATION = "policy_explanation"
    REFUND_PROCESSING = "refund_processing"
    ESCALATION_REQUEST = "escalation_request"
    PRODUCT_INQUIRY = "product_inquiry"
    TROUBLESHOOTING = "troubleshooting"
    COMPLAINT = "complaint"
    CANCELLATION = "cancellation"
    GENERAL = "general"


class DetectedIntent(BaseModel):
    intent_type: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_entities: dict[str, Any] = Field(default_factory=dict)


class PolicyViolation(BaseModel):
    rule_id: str
    rule_name: str
    description: str
    severity: Severity
    source_document: str | None = None
    source_section: str | None = None


class FactCheckResult(BaseModel):
    claim: str
    is_accurate: bool
    correct_value: str | None = None
    source_document: str | None = None
    source_page: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ValidationResult(BaseModel):
    is_compliant: bool
    action: CorrectionAction
    violations: list[PolicyViolation] = Field(default_factory=list)
    fact_errors: list[FactCheckResult] = Field(default_factory=list)
    severity: Severity = Severity.LOW


class CorrectionResult(BaseModel):
    original_text: str
    corrected_text: str
    was_modified: bool
    action_taken: CorrectionAction
    confidence: float = Field(ge=0.0, le=1.0)
    intent: DetectedIntent
    validation: ValidationResult
    explanation: ExplainabilityReport | None = None
    pii_detected: bool = False
    guardrails_applied: list[str] = Field(default_factory=list)
    detected_language: str = "en"


class ExplainabilityReport(BaseModel):
    correction_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rules_applied: list[str] = Field(default_factory=list)
    rationale: str
    source_documents: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    compliance_status: str


class CustomerContext(BaseModel):
    customer_id: str | None = None
    plan_type: str | None = None
    account_tenure_days: int | None = None
    account_tier: str | None = None
    days_since_purchase: int | None = None
    interaction_history: list[dict[str, Any]] = Field(default_factory=list)


class CorrectionEvent(BaseModel):
    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent_id: str | None = None
    session_id: str | None = None
    original_text: str
    corrected_text: str
    was_modified: bool
    action_taken: CorrectionAction
    intent: DetectedIntent
    violations: list[PolicyViolation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    latency_ms: float | None = None


# Resolve forward reference
CorrectionResult.model_rebuild()
