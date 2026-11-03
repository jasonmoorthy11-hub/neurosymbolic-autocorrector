"""Structured logging configuration with JSON output and correlation IDs."""

from __future__ import annotations

import json
import logging
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class CorrelationIdFilter(logging.Filter):
    """Injects request_id from contextvars into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("")  # type: ignore[attr-defined]
        return True


class JSONFormatter(logging.Formatter):
    """Emits log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            entry["request_id"] = request_id
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(entry, default=str)


def setup_logging() -> None:
    """Configure root logger from settings. Idempotent — safe to call multiple times."""
    from autocorrector.config import get_settings

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())

    if settings.log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))

    root.addHandler(handler)
