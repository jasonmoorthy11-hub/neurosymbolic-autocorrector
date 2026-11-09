"""One version number: `autocorrector.__version__`.

The package metadata, the dashboard API, `/health` and the dashboard's
`package.json` all have to say the same thing as the release tags and
`CHANGELOG.md`, so each of them is derived from or checked against that one string.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from autocorrector import __version__

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _as_semver(pep440: str) -> str:
    """`1.5.0.dev0` (PEP 440) is `1.5.0-dev.0` in npm's semver; releases are equal."""
    return re.sub(r"\.dev(\d+)$", r"-dev.\1", pep440)


def test_version_is_a_release_or_dev_version():
    # Release tags are vX.Y, so the patch number stays 0 and each release has its
    # own `## [X.Y]` changelog heading
    assert re.fullmatch(r"\d+\.\d+\.0(\.dev\d+)?", __version__)
    assert tuple(int(part) for part in __version__.split(".")[:2]) >= (1, 5)


def test_version_package_metadata_is_read_from_the_module():
    assert "version" not in PYPROJECT["project"]
    assert PYPROJECT["project"]["dynamic"] == ["version"]
    assert PYPROJECT["tool"]["hatch"]["version"]["path"] == "src/autocorrector/__init__.py"


def test_version_dashboard_api_reports_the_module_version():
    from autocorrector.dashboard.api import app

    assert app.version == __version__


def test_version_dashboard_package_matches():
    package = json.loads((ROOT / "dashboard" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "dashboard" / "package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == _as_semver(__version__)
    assert lock["version"] == package["version"]
    assert lock["packages"][""]["version"] == package["version"]


def test_version_changelog_has_an_entry_for_a_release():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for released in ("1.0", "1.1", "1.2", "1.3", "1.4"):
        assert f"## [{released}]" in changelog
    if ".dev" in __version__:
        assert "## [Unreleased]" in changelog
    else:
        assert f"## [{__version__.rsplit('.', 1)[0]}]" in changelog
