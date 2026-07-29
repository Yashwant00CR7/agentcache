"""Guardrails against version drift and missing release notes.

These tests exist because a stale-branch merge once silently downgraded the
package version (0.9.13 -> 0.9.12). They enforce the invariants that would
have caught that regression at PR time.
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import tomllib

import agentcache

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

DIST_NAME = "agentcache-core"


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def test_init_matches_pyproject():
    assert agentcache.__version__ == _pyproject_version(), (
        "src/agentcache/__init__.py __version__ is out of sync with "
        "pyproject.toml. Both must be bumped together."
    )


def test_installed_metadata_matches_init():
    try:
        installed = metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        # Editable install not present (e.g. running from a raw checkout);
        # nothing to compare against.
        return
    assert installed == agentcache.__version__, (
        f"Installed {DIST_NAME} metadata reports {installed}, but "
        f"agentcache.__version__ is {agentcache.__version__}. Reinstall "
        "with `pip install -e .` after bumping the version."
    )


def test_changelog_has_entry_for_current_version():
    version = _pyproject_version()
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(rf"^##\s*\[{re.escape(version)}\]", re.MULTILINE)
    assert pattern.search(text), (
        f"CHANGELOG.md is missing a '## [{version}]' heading. Every version "
        "bump must ship with release notes."
    )
