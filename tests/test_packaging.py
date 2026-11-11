"""The MCP server runs without the optional extras (guardrails-ai, langgraph, neo4j)."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.guardrails.pii import redact_pii
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.mcp_server import server

OPTIONAL = ("guardrails", "langgraph", "neo4j", "nemoguardrails", "presidio_analyzer")

# Runs in a fresh interpreter where importing an optional package fails, as it
# does in an environment installed without extras.
_WITHOUT_EXTRAS = textwrap.dedent(
    """
    import importlib.abc, importlib.util, sys

    BLOCKED = %r

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    sys.meta_path.insert(0, Block())
    real_find_spec = importlib.util.find_spec

    def find_spec(name, package=None):
        try:
            return real_find_spec(name, package)
        except ModuleNotFoundError:
            return None

    importlib.util.find_spec = find_spec

    from autocorrector.mcp_server import server

    assert not server._guardrails_installed()
    text, types = server._redact_pii("SSN 123-45-6789, mail a.b@example.com")
    assert text == "SSN [SSN REDACTED], mail [EMAIL REDACTED]", text
    assert types == ["ssn", "email"], types
    loaded = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not loaded, loaded
    print("ok")
    """
)


def test_packaging_mcp_server_imports_without_extras():
    done = subprocess.run(
        [sys.executable, "-c", _WITHOUT_EXTRAS % (OPTIONAL,)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "ok"


def _guard_redaction(text: str) -> tuple[str, list[str]]:
    result = build_pii_guard().validate(text)
    if result.validated_output and result.validated_output != text:
        return result.validated_output, server.detect_pii_types(text)
    return text, []


def test_packaging_redaction_is_the_same_with_and_without_guardrails():
    texts = [case.agent_text for case in GOLDEN_DATASET.cases]
    assert [redact_pii(t) for t in texts] == [_guard_redaction(t) for t in texts]


@pytest.mark.parametrize(
    "text",
    [
        "Your SSN 123-45-6789 is on file.",
        "Thanks for waiting, nothing private here.",
        "Ship to 72 Maple Way, Springfield, IL 62704 and call (555) 123-4567.",
    ],
)
def test_packaging_server_rail_falls_back_to_the_plain_patterns(monkeypatch, text):
    with_guard = server._redact_pii(text)
    monkeypatch.setattr(server, "_guardrails_installed", lambda: False)
    assert server._redact_pii(text) == with_guard
