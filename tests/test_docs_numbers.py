"""The golden-set size quoted in the docs is the size of the golden set.

`docs/case-study.md` once said 410/410 while the set had 421 cases. Every place
that states the current size is checked here, so adding a row fails this test
until the docs are updated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autocorrector.evaluation.dataset import GOLDEN_DATASET

ROOT = Path(__file__).resolve().parent.parent
# Wordings that state the current size of the golden set
COUNT_PATTERNS = (
    r"(\d+)-case golden",
    r"Golden, (\d+) cases",
    r"(\d+) golden cases",
    r"\b(\d+)/\1 actions correct",
    r"Accuracy:\s+100\.0%\s+\((\d+)/\1,",
    r"flag agrees:\s+100\.0%\s+\((\d+)/\1,",
)
# Files that must state it at least once
REQUIRED = ("README.md", "CLAUDE.md", "docs/evaluation.md", "docs/architecture.md")
SCANNED = sorted(
    {*REQUIRED, "scripts/demo.py", "dashboard/README.md"}
    | {str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md")}
)


def _quoted_counts(path: str) -> list[int]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return [int(m.group(1)) for pattern in COUNT_PATTERNS for m in re.finditer(pattern, text)]


@pytest.mark.parametrize("path", SCANNED)
def test_docs_quoted_golden_count_matches_dataset(path):
    assert set(_quoted_counts(path)) <= {len(GOLDEN_DATASET.cases)}


@pytest.mark.parametrize("path", REQUIRED)
def test_docs_state_the_golden_count(path):
    assert _quoted_counts(path), f"{path} no longer states the golden-set size"


def test_docs_name_the_dataset_version():
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"golden dataset v{GOLDEN_DATASET.version}" in claude_md
