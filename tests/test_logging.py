"""Tests for structured logging configuration."""

import json
import logging

from autocorrector.logging_config import (
    CorrelationIdFilter,
    JSONFormatter,
    request_id_var,
    setup_logging,
)


class TestJSONFormatter:
    def test_basic_output(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "hello world"
        assert "timestamp" in data
        assert "request_id" not in data

    def test_includes_request_id(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=None, exc_info=None,
        )
        record.request_id = "abc-123"  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["request_id"] == "abc-123"

    def test_excludes_empty_request_id(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=None, exc_info=None,
        )
        record.request_id = ""  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert "request_id" not in data

    def test_exception_formatting(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="failed", args=None, exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError: boom" in data["exception"]


class TestCorrelationIdFilter:
    def test_injects_from_contextvar(self):
        filt = CorrelationIdFilter()
        token = request_id_var.set("req-999")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="msg", args=None, exc_info=None,
            )
            filt.filter(record)
            assert record.request_id == "req-999"  # type: ignore[attr-defined]
        finally:
            request_id_var.reset(token)

    def test_empty_when_unset(self):
        filt = CorrelationIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=None, exc_info=None,
        )
        filt.filter(record)
        assert record.request_id == ""  # type: ignore[attr-defined]


class TestSetupLogging:
    def test_respects_log_level(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("AUTOCORRECTOR_LOG_FORMAT", "plain")
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1

    def test_json_mode(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_LOG_FORMAT", "json")
        setup_logging()
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_plain_mode(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_LOG_FORMAT", "plain")
        setup_logging()
        root = logging.getLogger()
        assert not isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_idempotent(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_LOG_FORMAT", "json")
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
