"""Tests for scripts/check_version_bump.py — the CI version guard.

Seam under test: ``main(base_ref: str) -> int``. The script's job is to
protect ``main`` from a stale-branch merge silently regressing the package
version, and from a version bump landing without matching release notes.

The failure mode we're locking down is the near-miss from PR #57
(chore/Remove-older-broken-codes): a branch that hadn't been rebased in a
long time and whose ``pyproject.toml`` still showed an older version. Git's
3-way merge saved us that time — these tests make sure CI catches it the
day it doesn't.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_version_bump.py"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("check_version_bump", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard_module()


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """Give the guard a fake repo layout it can read without hitting the real one."""
    pyproject = tmp_path / "pyproject.toml"
    init_py = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"

    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard, "PYPROJECT", pyproject)
    monkeypatch.setattr(guard, "INIT_PY", init_py)
    monkeypatch.setattr(guard, "CHANGELOG", changelog)

    def write(current_version: str, changelog_versions: list[str]) -> None:
        pyproject.write_text(
            f'[project]\nname = "agentcache-core"\nversion = "{current_version}"\n',
            encoding="utf-8",
        )
        init_py.write_text(f'__version__ = "{current_version}"\n', encoding="utf-8")
        headings = "\n\n".join(f"## [{v}]" for v in changelog_versions)
        changelog.write_text(f"# Changelog\n\n{headings}\n", encoding="utf-8")

    return write


@pytest.fixture
def stub_base(monkeypatch):
    """Stub _git_show so 'the base ref' returns whatever pyproject we choose."""

    def install(base_version: str | None) -> None:
        def fake(ref: str, path: Path) -> str | None:
            if base_version is None:
                return None
            if path.name == "pyproject.toml":
                return (
                    f'[project]\nname = "agentcache-core"\nversion = "{base_version}"\n'
                )
            return None

        monkeypatch.setattr(guard, "_git_show", fake)

    return install


def test_regression_fails_with_clear_message(fake_tree, stub_base, capsys):
    """The PR #57 scenario: branch is at an older version than main.

    Fails because we're trying to publish a lower version than what already
    exists on the base branch — this is the exact silent-downgrade the guard
    was written for.
    """
    fake_tree(current_version="0.9.11", changelog_versions=["0.9.13", "0.9.11"])
    stub_base("0.9.13")

    exit_code = guard.main("fake-main")

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "version regression" in stderr
    assert "0.9.11" in stderr
    assert "0.9.13" in stderr


def test_bump_without_changelog_entry_fails(fake_tree, stub_base, capsys):
    """A version bump must ship with a matching ``## [x.y.z]`` heading.

    Prevents the "shipped a version with no notes" class of bug.
    """
    fake_tree(current_version="0.9.14", changelog_versions=["0.9.13"])
    stub_base("0.9.13")

    exit_code = guard.main("fake-main")

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "CHANGELOG" in stderr
    assert "0.9.14" in stderr


def test_clean_bump_with_changelog_passes(fake_tree, stub_base, capsys):
    """The happy path: version bumped and CHANGELOG updated together."""
    fake_tree(current_version="0.9.14", changelog_versions=["0.9.14", "0.9.13"])
    stub_base("0.9.13")

    exit_code = guard.main("fake-main")

    assert exit_code == 0
    assert "OK" in capsys.readouterr().out


def test_pyproject_and_init_out_of_sync_fails(fake_tree, stub_base, capsys):
    """If __init__.py and pyproject.toml disagree, the guard must catch it.

    Belt-and-braces alongside tests/test_version.py, at the CI seam.
    """
    fake_tree(current_version="0.9.14", changelog_versions=["0.9.14"])
    # Overwrite __init__ so it disagrees with pyproject.
    guard.INIT_PY.write_text('__version__ = "0.9.13"\n', encoding="utf-8")
    stub_base("0.9.13")

    exit_code = guard.main("fake-main")

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "does not match" in stderr


def test_missing_base_ref_is_soft_skip(fake_tree, stub_base, capsys):
    """First-run / detached-CI case: no base ref available.

    We warn and pass rather than block the build — the pytest and other
    checks still enforce internal consistency.
    """
    fake_tree(current_version="0.9.14", changelog_versions=["0.9.14"])
    stub_base(None)

    exit_code = guard.main("does-not-exist")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "unavailable" in captured.err
