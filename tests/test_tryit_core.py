"""The browser panel's rule core: the file list it ships and the function it calls."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "dashboard" / "scripts" / "build-tryit.mjs"

_LOADED = """
import sys
sys.path.insert(0, {scripts!r})
import tryit_boot
print("\\n".join(sorted(m for m in sys.modules if m.split(".")[0] == "autocorrector")))
"""


def _shipped() -> set[str]:
    block = re.search(r"export const CORE = \[(.*?)\];", BUILD_SCRIPT.read_text(), re.S)
    assert block, "CORE list not found in build-tryit.mjs"
    return set(re.findall(r'"([^"]+\.py)"', block.group(1)))


def test_tryit_core_ships_exactly_what_the_runner_imports():
    """A new import in the runner's closure must be added to CORE (and one that
    leaves must be dropped), or the panel fails to load in the browser."""
    done = subprocess.run(
        [sys.executable, "-c", _LOADED.format(scripts=str(BUILD_SCRIPT.parent))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    src = ROOT / "src"
    imported = set()
    for module in done.stdout.split():
        as_path = Path(*module.split("."))
        package = src / as_path / "__init__.py"
        imported.add(str((as_path / "__init__.py") if package.exists() else as_path) + ".py")
    imported = {p.replace("/__init__.py.py", "/__init__.py") for p in imported}
    assert _shipped() == imported


def test_tryit_core_files_exist_and_leave_the_blind_set_out():
    for name in _shipped():
        assert (ROOT / "src" / name).is_file(), name
        assert "heldout" not in name and "evaluation" not in name


async def test_tryit_boot_check_gives_a_trace_record(monkeypatch):
    monkeypatch.syspath_prepend(str(BUILD_SCRIPT.parent))
    import tryit_boot

    record = json.loads(
        await tryit_boot.check(
            "I can offer you a $75 credit for your trouble.", '{"account_tenure_days": 30}'
        )
    )
    assert record["action"] == "correct"
    assert [v["rule_id"] for v in record["violations"]] == ["CREDIT-001"]
    assert record["detected_language"] == "en"
    assert "$50" in record["corrected_text"]
    assert {"edits", "pii_types", "fact_errors", "intent", "severity"} <= set(record)
