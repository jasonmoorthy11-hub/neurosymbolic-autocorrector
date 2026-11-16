#!/usr/bin/env python3
"""Check a built wheel: what it must contain and what it must leave out.

    python3 -m build --wheel
    python3 scripts/check_wheel.py dist/neurosymbolic_autocorrector-*.whl

Reads the archive only; nothing is installed or imported. Exit 1 lists what is wrong.
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
# The blind held-out set stays in the repository: a wheel that carried it would
# hand every installer the cases a new accuracy claim must not have seen.
FORBIDDEN = ("autocorrector/evaluation/heldout.py",)
ENTRY_POINT = "autocorrector = autocorrector.mcp_server.server:main"
# Extras stay extras: none of these may be a requirement without an `extra ==` marker.
OPTIONAL_PACKAGES = ("guardrails-ai", "langgraph", "neo4j", "nemoguardrails", "presidio-analyzer")


def problems(wheel: Path) -> list[str]:
    """Everything wrong with the wheel at ``wheel``; empty when it is fine."""
    found: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        found += [f"missing: {name}" for name in REQUIRED if name not in names]
        found += [f"must not ship: {name}" for name in FORBIDDEN if name in names]
        found += [f"not package code: {n}" for n in sorted(names) if n.startswith(("tests/", "."))]
        info = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        if not info or ENTRY_POINT not in archive.read(info[0]).decode():
            found.append(f"entry point not declared: {ENTRY_POINT}")
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        requires = [
            line.removeprefix("Requires-Dist: ").strip()
            for line in archive.read(metadata[0]).decode().splitlines()
            if line.startswith("Requires-Dist: ")
        ]
        for requirement in requires:
            name = re.match(r"[A-Za-z0-9._-]+", requirement).group().lower()
            if name in OPTIONAL_PACKAGES and "extra ==" not in requirement:
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
