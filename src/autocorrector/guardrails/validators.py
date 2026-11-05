"""Custom Guardrails AI validators for the Neurosymbolic Autocorrector.

Three validators that add deterministic symbolic rules to the pipeline:

1. ComplianceCheckValidator — checks agent output against policy rules via KG
2. FactualAccuracyValidator — verifies factual claims via KG
3. PIIDetectionValidator — detects and redacts PII in agent output

Each validator returns a FailResult with a fix_value when violations are found,
enabling automatic correction via Guard(on_fail='fix').
"""

from __future__ import annotations

import re
from typing import Any

from guardrails.validators import (
    FailResult,
    PassResult,
    Validator,
    register_validator,
)

from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.models import Severity

# ---------------------------------------------------------------------------
# 1. Compliance Check Validator
# ---------------------------------------------------------------------------


@register_validator(name="compliance-check", data_type="string")
class ComplianceCheckValidator(Validator):
    """Validates agent output against policy compliance rules.

    Uses the Knowledge Graph Store to check for policy violations
    (credit thresholds, authorization levels, procedure compliance)
    and generates corrected text when violations are found.

    Metadata keys:
        kg_store: KnowledgeGraphStore instance
        intent_type: str — classified intent type
        customer_context: dict — customer info (tenure, plan, etc.)
    """

    required_metadata_keys = ["kg_store", "intent_type", "customer_context"]

    def validate(self, value: Any, metadata: dict | None = None) -> PassResult | FailResult:
        """Synchronous wrapper — delegates to the KG store's in-memory check."""
        metadata = metadata or {}
        kg: KnowledgeGraphStore = metadata["kg_store"]
        intent_type: str = metadata["intent_type"]
        customer_context: dict = metadata["customer_context"]

        violations = kg._check_compliance_inmemory(
            text=str(value),
            intent_type=intent_type,
            customer_context=customer_context,
        )

        if not violations:
            return PassResult()

        # Determine max severity
        severity_order = {
            Severity.LOW: 0,
            Severity.MEDIUM: 1,
            Severity.HIGH: 2,
            Severity.CRITICAL: 3,
        }
        max_severity = max(violations, key=lambda v: severity_order.get(v.severity, 0)).severity

        # Generate corrected text
        violation_dicts = [v.model_dump() for v in violations]
        corrected = str(value)
        for v in violations:
            template = kg._get_template_correction(v.rule_id, customer_context, str(value))
            if template:
                corrected = template
                break

        error_parts = [
            f"{v.rule_id}: {v.description} (severity: {v.severity.value})" for v in violations
        ]
        error_msg = f"Policy violations found: {'; '.join(error_parts)}"

        return FailResult(
            error_message=error_msg,
            fix_value=corrected,
            metadata={
                "violations": violation_dicts,
                "max_severity": max_severity.value,
                "violation_count": len(violations),
            },
        )


# ---------------------------------------------------------------------------
# 2. Factual Accuracy Validator
# ---------------------------------------------------------------------------


@register_validator(name="factual-accuracy", data_type="string")
class FactualAccuracyValidator(Validator):
    """Validates factual claims in agent output against the Knowledge Graph.

    Checks dollar amounts, coverage limits, plan details, etc. against
    stored ground truth. Returns corrected text with accurate values.

    Metadata keys:
        kg_store: KnowledgeGraphStore instance
        customer_context: dict — must include plan_type for fact checking
    """

    required_metadata_keys = ["kg_store", "customer_context"]

    def validate(self, value: Any, metadata: dict | None = None) -> PassResult | FailResult:
        """Synchronous wrapper — delegates to the KG store's in-memory check."""
        metadata = metadata or {}
        kg: KnowledgeGraphStore = metadata["kg_store"]
        customer_context: dict = metadata["customer_context"]

        fact_results = kg._verify_facts_inmemory(
            text=str(value),
            customer_context=customer_context,
        )
        errors = [f for f in fact_results if not f.is_accurate]

        if not errors:
            return PassResult()

        # Apply fact corrections — replace wrong values with correct ones
        corrected = str(value)
        for error in errors:
            if error.claim and error.correct_value:
                corrected = corrected.replace(error.claim, error.correct_value)

        error_parts = [
            f"Claimed {e.claim}, correct value: {e.correct_value} "
            f"(source: {e.source_document}, p.{e.source_page})"
            for e in errors
        ]
        error_msg = f"Factual errors found: {'; '.join(error_parts)}"

        return FailResult(
            error_message=error_msg,
            fix_value=corrected,
            metadata={
                "fact_errors": [e.model_dump(mode="json") for e in errors],
                "error_count": len(errors),
            },
        )


# ---------------------------------------------------------------------------
# 3. PII Detection Validator
# ---------------------------------------------------------------------------

# Common PII patterns — ordered so prefix-based patterns run before general numeric ones
_PII_PATTERNS = {
    "ssn": (
        r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b",
        "[SSN REDACTED]",
    ),
    "credit_card": (
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "[CARD REDACTED]",
    ),
    "email": (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL REDACTED]",
    ),
    "date_of_birth": (
        # The date may trail its cue by a few words ("date of birth you gave us, 04/17/1986")
        # but not cross into the next sentence
        r"\b(?:DOB|date of birth|birth ?date|born on)\b[^\d\n.;!?]{0,40}?"
        r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
        "[DOB REDACTED]",
    ),
    "passport": (
        # The lookahead keeps a redaction token from matching as the number on a second pass
        r"\b(?:passport\s*(?:number|no\.?|#)?[:\s]*)(?!REDACTED\])[A-Z0-9]{6,9}\b",
        "[PASSPORT REDACTED]",
    ),
    "bank_account": (
        r"\b(?:account\s*(?:number|no\.?|#)|acct\.?\s*#?)[:\s]*\d{8,17}\b",
        "[BANK ACCOUNT REDACTED]",
    ),
    "routing_number": (
        r"\b(?:routing\s*(?:number|no\.?|#)|ABA)[:\s]*\d{9}\b",
        "[ROUTING NUMBER REDACTED]",
    ),
    "drivers_license": (
        r"\b(?:driver'?s?\s*license\s*(?:number|no\.?|#)?|DL\s*#?)[:\s]*"
        r"(?!REDACTED\])[A-Z0-9]{5,15}\b",
        "[DL REDACTED]",
    ),
    "address": (
        r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St(?:reet)?|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Rd|Road|Way|Ct|Court|Pl(?:ace)?|Cir(?:cle)?)\.?(?:\s*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?\b",
        "[ADDRESS REDACTED]",
    ),
    "phone": (
        r"(?<!\w)(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "[PHONE REDACTED]",
    ),
}
PII_TYPES = tuple(_PII_PATTERNS)


def detect_pii_types(text: str) -> list[str]:
    """PII types present in ``text``, in pattern order (what PIIDetectionValidator redacts)."""
    return [
        pii_type
        for pii_type, (pattern, _) in _PII_PATTERNS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def find_pii_spans(text: str) -> list[tuple[str, int, int]]:
    """(type, start, end) of each span PIIDetectionValidator redacts, sorted by start.

    Replays the validator exactly: patterns run in order, each only if it matches
    the original text, each substituting on the already-redacted text. Positions
    are mapped back to ``text``; a match that swallows earlier redaction tokens
    absorbs those spans. ``redact_spans(text, find_pii_spans(text))`` therefore
    equals the validator's output.
    """
    current = text
    # origin[i]: index in ``text`` of current[i], or -(claim id + 1) for token chars
    origin = list(range(len(text)))
    claims: dict[int, tuple[str, int, int]] = {}
    next_id = 0
    for pii_type, (pattern, token) in _PII_PATTERNS.items():
        if not re.search(pattern, text, re.IGNORECASE):
            continue
        pieces: list[str] = []
        new_origin: list[int] = []
        pos = 0
        for m in re.finditer(pattern, current, re.IGNORECASE):
            starts, ends = [], []
            for o in origin[m.start() : m.end()]:
                if o >= 0:
                    starts.append(o)
                    ends.append(o + 1)
                elif -o - 1 in claims:
                    _, s, e = claims.pop(-o - 1)
                    starts.append(s)
                    ends.append(e)
            claim_id = next_id
            next_id += 1
            claims[claim_id] = (pii_type, min(starts), max(ends))
            pieces += [current[pos : m.start()], token]
            new_origin += origin[pos : m.start()] + [-claim_id - 1] * len(token)
            pos = m.end()
        pieces.append(current[pos:])
        new_origin += origin[pos:]
        current, origin = "".join(pieces), new_origin
    return sorted(claims.values(), key=lambda span: span[1])


def redact_spans(text: str, spans: list[tuple[str, int, int]]) -> str:
    """Replace each (type, start, end) span with that type's redaction token."""
    out, pos = [], 0
    for pii_type, start, end in sorted(spans, key=lambda span: span[1]):
        out.append(text[pos:start])
        out.append(_PII_PATTERNS[pii_type][1])
        pos = end
    out.append(text[pos:])
    return "".join(out)


@register_validator(name="pii-detection", data_type="string")
class PIIDetectionValidator(Validator):
    """Detects and redacts personally identifiable information in agent output.

    Scans for SSNs, credit card numbers, email addresses, phone numbers,
    and dates of birth. Returns redacted text when PII is found.

    This is a critical safety rail — agents should never include PII
    in their responses as it creates compliance and security risks.
    """

    def __init__(
        self,
        pii_types: list[str] | None = None,
        on_fail: Any = None,
        **kwargs: Any,
    ):
        super().__init__(on_fail=on_fail, **kwargs)
        # Which PII types to check (default: all)
        self._pii_types = pii_types or list(_PII_PATTERNS.keys())

    def validate(self, value: Any, metadata: dict | None = None) -> PassResult | FailResult:
        """Check for PII patterns and redact if found."""
        text = str(value)
        found_pii: list[dict[str, str]] = []
        redacted = text

        for pii_type in self._pii_types:
            if pii_type not in _PII_PATTERNS:
                continue
            pattern, replacement = _PII_PATTERNS[pii_type]
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                for match in matches:
                    found_pii.append({"type": pii_type, "value": match})
                redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)

        if not found_pii:
            return PassResult()

        pii_types_found = list({p["type"] for p in found_pii})
        error_msg = f"PII detected: {', '.join(pii_types_found)} ({len(found_pii)} instance(s))"

        return FailResult(
            error_message=error_msg,
            fix_value=redacted,
            metadata={
                "pii_found": pii_types_found,
                "pii_count": len(found_pii),
            },
        )
