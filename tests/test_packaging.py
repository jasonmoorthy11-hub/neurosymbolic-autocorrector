"""The MCP server runs without the optional extras (guardrails-ai, langgraph, neo4j).

These tests hide named packages from an environment that has them. The proof that
nothing else is needed is the CI job that installs the wheel alone and runs the
server (`wheel` in .github/workflows/ci.yml).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
import zipfile
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.guardrails.pii import redact_pii
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.mcp_server import server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_wheel import ENTRY_POINT, REQUIRED, problems  # noqa: E402

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

    assert server._pii_guard_builder() is None
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


_WITHOUT_HELDOUT = textwrap.dedent(
    """
    import importlib.abc, sys

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "autocorrector.evaluation.heldout":
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    sys.meta_path.insert(0, Block())
    from autocorrector.evaluation.registry import DATASETS

    assert sorted(DATASETS) == ["golden"], sorted(DATASETS)
    print("ok")
    """
)


def test_packaging_registry_offers_the_golden_set_when_the_blind_set_is_absent():
    done = subprocess.run(
        [sys.executable, "-c", _WITHOUT_HELDOUT], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "ok"


def test_packaging_wheel_excludes_the_blind_set():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "src/autocorrector/evaluation/heldout.py" in wheel["exclude"]


def _fake_wheel(path: Path, names: list[str], requires: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "")
        archive.writestr("x-1.dist-info/entry_points.txt", f"[console_scripts]\n{ENTRY_POINT}\n")
        archive.writestr(
            "x-1.dist-info/METADATA", "".join(f"Requires-Dist: {r}\n" for r in requires)
        )
    return path


def test_packaging_check_wheel_accepts_a_good_wheel(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "ok.whl", list(REQUIRED), ["mcp<2,>=1.25", "neo4j<7,>=6.1; extra == 'neo4j'"]
    )
    assert problems(wheel) == []


def test_packaging_check_wheel_names_what_is_wrong(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "bad.whl",
        [*REQUIRED[1:], "autocorrector/evaluation/heldout.py", "tests/test_x.py"],
        ["guardrails-ai>=0.9"],
    )
    assert problems(wheel) == [
        f"missing: {REQUIRED[0]}",
        "must not ship: autocorrector/evaluation/heldout.py",
        "not package code: tests/test_x.py",
        "optional package is a base requirement: guardrails-ai>=0.9",
    ]


def test_packaging_redaction_is_the_same_with_and_without_guardrails():
    assert server._pii_guard_builder() is build_pii_guard  # this run has the extra
    texts = [case.agent_text for case in GOLDEN_DATASET.cases]
    assert [redact_pii(t) for t in texts] == [server._redact_pii(t) for t in texts]


# guardrails-ai resolves but does not import: here the name finds this package's
# own `guardrails/` directory, because the interpreter starts inside src/autocorrector.
_SHADOWED = textwrap.dedent(
    """
    import logging, sys
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(levelname)s %(message)s")
    from autocorrector.mcp_server import server

    for _ in range(3):
        assert server._redact_pii("SSN 123-45-6789") == ("SSN [SSN REDACTED]", ["ssn"])
    """
)


def test_packaging_server_rail_survives_a_guardrails_that_does_not_import():
    package_dir = ROOT / "src" / "autocorrector"
    done = subprocess.run(
        [sys.executable, "-c", _SHADOWED],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=package_dir,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    warnings = [line for line in done.stdout.splitlines() if line.startswith("WARNING")]
    assert len(warnings) == 1, done.stdout  # said once, not on every call
    assert "guardrails" in warnings[0]


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
    monkeypatch.setattr(server, "_pii_guard_builder", lambda: None)
    assert server._redact_pii(text) == with_guard
