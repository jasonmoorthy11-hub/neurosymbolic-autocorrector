"""The MCP server runs without the optional extras (guardrails-ai, langgraph, neo4j).

These tests hide named packages from an environment that has them. The proof that
nothing else is needed is the CI job that installs the wheel alone and runs the
server (`wheel` in .github/workflows/ci.yml).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import tomllib
import types
import zipfile
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET
from autocorrector.guardrails.pii import redact_pii
from autocorrector.guardrails.telemetry import build_pii_guard
from autocorrector.mcp_server import server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_wheel import ENTRY_POINTS, REQUIRED, problems  # noqa: E402

OPTIONAL = (
    "guardrails",
    "langgraph",
    "neo4j",
    "nemoguardrails",
    "presidio_analyzer",
    "fastapi",
    "slowapi",
    "anthropic",
    "openai",
)

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


def test_packaging_server_json_fits_the_registry_rules():
    """The MCP registry's stated limits (schema 2025-12-11); the registry is not called."""
    from autocorrector import __version__

    meta = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"io\.github\.jasonmoorthy11-hub/[a-zA-Z0-9._-]+", meta["name"])
    assert 1 <= len(meta["description"]) <= 100
    assert 1 <= len(meta["title"]) <= 100
    release = re.sub(r"\.dev\d+$", "", __version__)  # server.json names the release being prepared
    package = meta["packages"][0]
    assert meta["version"] == package["version"] == release
    assert (package["registryType"], package["transport"]) == ("pypi", {"type": "stdio"})
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert package["identifier"] == project["name"]
    # PyPI ownership check: the marker must be in the README that becomes the description
    assert project["readme"] == "README.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {meta['name']} -->" in readme


_DECLARED = "\n".join(f"{name} = {target}" for name, target in ENTRY_POINTS.items())
_UNDECLARED = [f"entry point not declared: {n} = {t}" for n, t in ENTRY_POINTS.items()]


def test_packaging_both_command_names_start_the_server():
    """`uvx neurosymbolic-autocorrector` (server.json) looks for a command of that name."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == ENTRY_POINTS
    assert set(ENTRY_POINTS) == {"autocorrector", project["name"]}
    assert len(set(ENTRY_POINTS.values())) == 1


def _fake_wheel(
    path: Path,
    names: list[str],
    requires: list[str] | None,
    entry_points: str = f"[console_scripts]\n{_DECLARED}\n",
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "")
        if entry_points:
            archive.writestr("x-1.dist-info/entry_points.txt", entry_points)
        if requires is not None:
            archive.writestr(
                "x-1.dist-info/METADATA", "".join(f"Requires-Dist: {r}\n" for r in requires)
            )
    return path


def test_packaging_check_wheel_accepts_a_good_wheel(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "ok.whl",
        list(REQUIRED),
        ["mcp<2,>=1.25", "neo4j<7,>=6.1; extra == 'neo4j'", "langgraph>=1; extra=='langgraph'"],
    )
    assert problems(wheel) == []


def test_packaging_check_wheel_names_what_is_wrong(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "bad.whl",
        [
            *REQUIRED[1:],
            "autocorrector/evaluation/heldout.py",
            "autocorrector/evaluation/__pycache__/heldout.cpython-312.pyc",
            "tests/test_x.py",
            "scripts/x.py",
        ],
        ["guardrails_ai>=0.9", "Presidio.Analyzer<3", "(bad)"],
        entry_points="[console_scripts]\n"
        + "".join(f"# {line}\n" for line in _DECLARED.splitlines()),
    )
    assert problems(wheel) == [
        f"missing: {REQUIRED[0]}",
        "must not ship: autocorrector/evaluation/__pycache__/heldout.cpython-312.pyc",
        "must not ship: autocorrector/evaluation/heldout.py",
        "not package code: scripts/x.py",
        "not package code: tests/test_x.py",
        *_UNDECLARED,
        "optional package is a base requirement: guardrails_ai>=0.9",
        "optional package is a base requirement: Presidio.Analyzer<3",
        "unreadable requirement: (bad)",
    ]


def test_packaging_check_wheel_reports_missing_metadata_files(tmp_path):
    wheel = _fake_wheel(tmp_path / "bare.whl", list(REQUIRED), None, entry_points="")
    assert problems(wheel) == [*_UNDECLARED, "no METADATA file"]


@pytest.mark.parametrize(
    "name",
    [
        "autocorrector/evaluation/HeldOut.py",
        "autocorrector/evaluation/held_out.py",
        "autocorrector/evaluation/held-out.json",
    ],
)
def test_packaging_check_wheel_catches_other_spellings_of_the_blind_set(tmp_path, name):
    wheel = _fake_wheel(tmp_path / "w.whl", [*REQUIRED, name], [])
    assert problems(wheel) == [f"must not ship: {name}"]


def test_packaging_check_wheel_rejects_code_under_a_nested_dist_info(tmp_path):
    wheel = _fake_wheel(tmp_path / "w.whl", [*REQUIRED, "evil/x.dist-info/payload.py"], [])
    assert problems(wheel) == ["not package code: evil/x.dist-info/payload.py"]


def test_packaging_check_wheel_rejects_a_second_dist_info(tmp_path):
    wheel = _fake_wheel(tmp_path / "w.whl", [*REQUIRED, "y-2.dist-info/METADATA"], [])
    assert problems(wheel) == ["more than one .dist-info directory: x-1.dist-info, y-2.dist-info"]


def test_packaging_check_wheel_wants_console_scripts(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "w.whl", list(REQUIRED), [], entry_points=f"[gui_scripts]\n{_DECLARED}\n"
    )
    assert problems(wheel) == _UNDECLARED


@pytest.mark.parametrize(
    "requirement",
    [
        "anthropic; python_version > '3' or extra == 'llm'",
        "openai>=1; extra == 'llm' or sys_platform == 'linux'",
    ],
)
def test_packaging_check_wheel_rejects_an_extra_marker_that_is_optional(tmp_path, requirement):
    wheel = _fake_wheel(tmp_path / "w.whl", list(REQUIRED), [requirement])
    assert problems(wheel) == [f"optional package is a base requirement: {requirement}"]


def test_packaging_check_wheel_rejects_a_base_requirement_that_asks_for_extras(tmp_path):
    """`uvicorn[standard]` in base would pull uvloop, httptools and websockets in."""
    wheel = _fake_wheel(tmp_path / "w.whl", list(REQUIRED), ["uvicorn[standard]>=0.34"])
    assert problems(wheel) == ["base requirement asks for extras: uvicorn[standard]>=0.34"]
    fine = ["uvicorn>=0.34", "uvicorn[standard]>=0.34; extra == 'dashboard'"]
    assert problems(_fake_wheel(tmp_path / "ok.whl", list(REQUIRED), fine)) == []


def test_packaging_check_wheel_reads_headers_not_the_description(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "w.whl",
        list(REQUIRED),
        ["mcp>=1.25\n\nRequires-Dist: anthropic (in the README)"],
    )
    assert problems(wheel) == []


def test_packaging_dashboard_extra_brings_what_the_pipeline_imports():
    """The dashboard API runs the pipeline, which imports langgraph and the Guardrails wrapper."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    own = [r for r in extras["dashboard"] if r.startswith("neurosymbolic-autocorrector[")]
    assert own == ["neurosymbolic-autocorrector[langgraph,guardrails]"]


def test_packaging_check_wheel_accepts_an_extra_marker_joined_with_and(tmp_path):
    wheel = _fake_wheel(
        tmp_path / "w.whl",
        list(REQUIRED),
        ["guardrails-ai>=0.9; python_version < '3.14' and extra == 'guardrails'"],
    )
    assert problems(wheel) == []


class _RaisesOnImport(types.ModuleType):
    """Stands in for `guardrails/telemetry.py`: `from … import name` raises `error`."""

    def __init__(self, error: Exception) -> None:
        super().__init__("autocorrector.guardrails.telemetry")
        self.__dict__["_error"] = error

    def __getattr__(self, name: str):
        raise self.__dict__["_error"]


@pytest.mark.parametrize(
    ("error", "level"),
    [
        (ModuleNotFoundError("No module named 'guardrails'", name="guardrails"), logging.INFO),
        (
            ModuleNotFoundError("No module named 'opentelemetry'", name="opentelemetry"),
            logging.WARNING,
        ),
        (ImportError("cannot import name 'Guard'"), logging.WARNING),
        (RuntimeError("broken on import"), logging.WARNING),
    ],
)
def test_packaging_guard_builder_says_absent_quietly_and_broken_loudly(
    monkeypatch, caplog, error, level
):
    monkeypatch.setitem(sys.modules, "autocorrector.guardrails.telemetry", _RaisesOnImport(error))
    uncached = server._pii_guard_builder.__wrapped__  # leave the process-wide answer alone
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        assert uncached() is None
    assert [r.levelno for r in caplog.records] == [level]
    assert "guardrails-ai is not importable" in caplog.records[0].getMessage()


def test_packaging_publish_workflow_is_manual_and_builds_the_tag():
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    triggers = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  (\w+):", triggers, flags=re.M) == ["workflow_dispatch"]
    assert "ref: refs/tags/${{ inputs.tag }}" in workflow  # a tag, never a branch or a sha
    # No expression is pasted into a shell line: inputs reach scripts as environment values
    in_run = False
    for line in workflow.splitlines():
        if re.match(r"\s+(- )?run:", line):
            in_run = True
        elif re.match(r"\s+(- )?(uses|name|with|env|if):", line):
            in_run = False
        assert not (in_run and "${{" in line), line
    # Every action in the job that holds the publishing identity is pinned to a commit
    upload_job = workflow.split("\n  publish:\n", 1)[1]
    used = re.findall(r"uses: (\S+)", upload_job)
    assert len(used) == 2 and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in used), used


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
