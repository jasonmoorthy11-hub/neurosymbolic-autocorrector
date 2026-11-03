"""Build Guardrails AI guards with hub telemetry off by default.

guardrails-ai enables anonymous metrics when no ``~/.guardrailsrc`` exists and
exports OTLP spans to a hosted collector. ``Guard()`` re-reads that file into the
global ``settings.rc`` on construction, and each validator reads
``settings.rc.enable_metrics`` when it is constructed, so the order below matters:
configure the guard, then override the rc value, then build the validator.
"""

from __future__ import annotations

from typing import Any

from guardrails import Guard
from guardrails.settings import settings as guardrails_settings
from guardrails.utils.hub_telemetry_utils import HubTelemetry
from guardrails.validator_base import Validator

from autocorrector.config import get_settings
from autocorrector.guardrails.validators import PIIDetectionValidator


def build_guard(validator_cls: type[Validator], **validator_kwargs: Any) -> Guard:
    """Return a Guard with one validator.

    Hub telemetry follows ``AUTOCORRECTOR_GUARDRAILS_TELEMETRY`` (default off).
    """
    allow_metrics = get_settings().guardrails_telemetry
    guard = Guard()
    guard.configure(allow_metrics_collection=allow_metrics)
    guardrails_settings.rc.enable_metrics = allow_metrics
    if not allow_metrics:
        # HubTelemetry()._enabled is process-wide and any bare Guard() sets it back
        # to the rc default, so a concurrent request could still trace. The trace
        # wrappers only emit when _tracer is set, and Guard never re-creates it.
        # Consequence: opting in only takes effect if set before the first guard is built.
        HubTelemetry()._tracer = None
    return guard.use(validator_cls(**validator_kwargs))


def build_pii_guard(pii_types: list[str] | None = None) -> Guard:
    """Return a PII-redacting Guard (``on_fail="fix"``) with telemetry off by default."""
    return build_guard(PIIDetectionValidator, pii_types=pii_types, on_fail="fix")
