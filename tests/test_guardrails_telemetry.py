"""Guardrails AI hub telemetry must stay off unless explicitly enabled.

Without a ``~/.guardrailsrc``, guardrails-ai defaults ``enable_metrics`` to True and
exports OTLP spans to a hosted collector. These tests run with an empty HOME so that
default applies, and replace the HubTelemetry tracer with a recorder so no span can
leave the process.

The tracing switch (``HubTelemetry()._enabled``) is process-wide and every bare
``Guard()`` flips it back on, so the off path also drops the shared tracer: the
``trace`` wrappers only emit when ``_tracer is not None``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from guardrails import Guard
from guardrails.settings import settings as guardrails_settings
from guardrails.utils.hub_telemetry_utils import HubTelemetry

from autocorrector.config import get_settings
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.guardrails.validators import PIIDetectionValidator
from autocorrector.orchestrator.pipeline import run_autocorrection


@pytest.fixture
def span_recorder(monkeypatch, tmp_path) -> MagicMock:
    """Empty HOME (no .guardrailsrc -> metrics default on) plus a recording tracer.

    Shared Guardrails state touched by these tests is restored afterwards.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    hub = HubTelemetry()
    monkeypatch.setattr(hub, "_enabled", hub._enabled)
    monkeypatch.setattr(guardrails_settings, "rc", guardrails_settings.rc)
    recorder = MagicMock()
    monkeypatch.setattr(hub, "_tracer", recorder)
    return recorder


def test_guardrails_telemetry_control_plain_guard_emits_spans(span_recorder):
    """Control: an unconfigured Guard would trace, so the recorder can see it."""
    guard = Guard().use(PIIDetectionValidator(on_fail="fix"))
    guard.validate("Your SSN is 123-45-6789.")
    assert span_recorder.start_span.called


def test_guardrails_telemetry_build_pii_guard_disables_metrics(span_recorder):
    guard = build_pii_guard()
    result = guard.validate("Your SSN is 123-45-6789.")

    assert "[SSN REDACTED]" in result.validated_output
    assert guard._allow_metrics_collection is False
    assert all(v._disable_telemetry for v in guard._validators)
    assert HubTelemetry()._tracer is None
    span_recorder.start_span.assert_not_called()


def test_guardrails_telemetry_off_survives_shared_flag_flip(span_recorder):
    """A bare Guard() elsewhere re-enables the shared flag; no tracer means no spans."""
    guard = build_pii_guard()
    Guard()  # flips HubTelemetry()._enabled back to the rc default (True)
    assert HubTelemetry()._enabled is True
    guard.validate("Your SSN is 123-45-6789.")
    assert HubTelemetry()._tracer is None
    span_recorder.start_span.assert_not_called()


async def test_guardrails_telemetry_pipeline_emits_no_spans(span_recorder):
    result = await run_autocorrection(
        "Your SSN is 123-45-6789 and email john.doe@example.com.",
        {"customer_id": "C-1", "account_tenure_days": 200},
    )
    assert "123-45-6789" not in (result.corrected_text or "")
    assert HubTelemetry()._tracer is None
    span_recorder.start_span.assert_not_called()


def test_guardrails_telemetry_opt_in_setting(span_recorder, monkeypatch):
    monkeypatch.setenv("AUTOCORRECTOR_GUARDRAILS_TELEMETRY", "true")
    assert get_settings().guardrails_telemetry is True
    guard = build_pii_guard()
    guard.validate("Your SSN is 123-45-6789.")
    assert guard._allow_metrics_collection is True
    assert span_recorder.start_span.called
