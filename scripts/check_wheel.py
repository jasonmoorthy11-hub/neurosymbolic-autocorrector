#!/usr/bin/env python3
"""Check a built wheel: what it must contain and what it must leave out.

    python3 -m build --wheel
    python3 scripts/check_wheel.py dist/neurosymbolic_autocorrector-*.whl

Reads the archive only; nothing is installed or imported. Exit 1 lists what is wrong.

The blind held-out set stays in the repository: no file with "heldout" in its
name may be in the wheel, and nothing outside the package and its metadata.
"""

from __future__ import annotations

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
ENTRY_POINT = "autocorrector = autocorrector.mcp_server.server:main"
# Extras stay extras: none of these may be a requirement without an `extra ==` marker.
OPTIONAL_PACKAGES = ("guardrails-ai", "langgraph", "neo4j", "nemoguardrails", "presidio-analyzer")


def _normalised(name: str) -> str:
    """A project name as PyPI compares them: lower case, runs of ``-_.`` as one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(archive: zipfile.ZipFile, names: set[str], suffix: str) -> str | None:
    matches = [name for name in names if name.endswith(suffix)]
    return archive.read(matches[0]).decode() if matches else None


def problems(wheel: Path) -> list[str]:
    """Everything wrong with the wheel at ``wheel``; empty when it is fine."""
    found: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        found += [f"missing: {name}" for name in REQUIRED if name not in names]
        found += [f"must not ship: {name}" for name in sorted(names) if "heldout" in name]
        found += [
            f"not package code: {name}"
            for name in sorted(names)
            if not name.startswith("autocorrector/") and ".dist-info/" not in name
        ]
        entry_points = _read(archive, names, ".dist-info/entry_points.txt")
        declared = [line.strip() for line in (entry_points or "").splitlines()]
        if ENTRY_POINT not in declared:
            found.append(f"entry point not declared: {ENTRY_POINT}")
        metadata = _read(archive, names, ".dist-info/METADATA")
        if metadata is None:
            return [*found, "no METADATA file"]
        for line in metadata.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            requirement = line.removeprefix("Requires-Dist:").strip()
            name = re.match(r"[A-Za-z0-9._-]+", requirement)
            if name is None:
                found.append(f"unreadable requirement: {requirement}")
            elif _normalised(name.group()) in OPTIONAL_PACKAGES and not re.search(
                r"\bextra\s*==", requirement
            ):
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
