#!/usr/bin/env python3
"""Check a built wheel: what it must contain and what it must leave out.

    python3 -m build --wheel
    python3 scripts/check_wheel.py dist/neurosymbolic_autocorrector-*.whl

Reads the archive only; nothing is installed or imported. Exit 1 lists what is wrong.

The blind held-out set stays in the repository: no file with "heldout" in its
name (in any case, with or without a separator) may be in the wheel, and nothing
outside the package and its one top-level metadata directory.
"""

from __future__ import annotations

import configparser
import re
import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "autocorrector/__init__.py",
    "autocorrector/mcp_server/server.py",
    "autocorrector/guardrails/pii.py",
    "autocorrector/evaluation/registry.py",
    "autocorrector/evaluation/dataset.py",
)
# Two names for one command: `autocorrector`, and the project name, which is what
# `uvx neurosymbolic-autocorrector` (server.json) looks for.
ENTRY_POINTS = {
    "autocorrector": "autocorrector.mcp_server.server:main",
    "neurosymbolic-autocorrector": "autocorrector.mcp_server.server:main",
}
_DIST_INFO = re.compile(r"([^/]+\.dist-info)/")
# Extras stay extras: none of these may be a requirement without an `extra ==` marker.
OPTIONAL_PACKAGES = (
    "guardrails-ai",
    "langgraph",
    "neo4j",
    "nemoguardrails",
    "presidio-analyzer",
    "fastapi",
    "slowapi",
    "anthropic",
    "openai",
)


def _normalised(name: str) -> str:
    """A project name as PyPI compares them: lower case, runs of ``-_.`` as one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_blind_set(name: str) -> bool:
    return "heldout" in re.sub(r"[-_. ]", "", name.lower())


def _console_scripts(entry_points: str | None) -> dict[str, str]:
    parser = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    parser.optionxform = str  # command names keep their case
    try:
        parser.read_string(entry_points or "")
    except configparser.Error:
        return {}
    return dict(parser["console_scripts"]) if parser.has_section("console_scripts") else {}


def _extra_only(requirement: str) -> bool:
    """Whether the requirement applies only when an extra is asked for."""
    _, _, marker = requirement.partition(";")
    return bool(re.search(r"\bextra\s*==", marker)) and not re.search(r"\bor\b", marker)


def problems(wheel: Path) -> list[str]:
    """Everything wrong with the wheel at ``wheel``; empty when it is fine."""
    found: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        found += [f"missing: {name}" for name in REQUIRED if name not in names]
        found += [f"must not ship: {name}" for name in sorted(names) if _is_blind_set(name)]
        found += [
            f"not package code: {name}"
            for name in sorted(names)
            if not name.startswith("autocorrector/") and not _DIST_INFO.match(name)
        ]
        dist_infos = sorted({m.group(1) for m in map(_DIST_INFO.match, names) if m})
        if len(dist_infos) > 1:
            return [*found, f"more than one .dist-info directory: {', '.join(dist_infos)}"]

        def read(file: str) -> str | None:
            name = f"{dist_infos[0]}/{file}" if dist_infos else ""
            return archive.read(name).decode() if name in names else None

        declared = _console_scripts(read("entry_points.txt"))
        found += [
            f"entry point not declared: {name} = {target}"
            for name, target in ENTRY_POINTS.items()
            if declared.get(name) != target
        ]
        metadata = read("METADATA")
        if metadata is None:
            return [*found, "no METADATA file"]
        for line in metadata.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            requirement = line.removeprefix("Requires-Dist:").strip()
            name = re.match(r"[A-Za-z0-9._-]+", requirement)
            if name is None:
                found.append(f"unreadable requirement: {requirement}")
            elif _normalised(name.group()) in OPTIONAL_PACKAGES and not _extra_only(requirement):
                found.append(f"optional package is a base requirement: {requirement}")
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    wheel = Path(sys.argv[1])
    found = problems(wheel)
    for line in found:
        print(line)
    if not found:
        print(f"{wheel.name}: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
