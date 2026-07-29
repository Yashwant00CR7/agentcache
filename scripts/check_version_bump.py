#!/usr/bin/env python3
"""Guard against version regressions and missing CHANGELOG entries.

Compares the current tree's package version against a base ref (default
``origin/main``) and enforces two rules:

  1. The version in ``pyproject.toml`` and ``src/agentcache/__init__.py`` must
     match each other, and must be greater than or equal to the base version.
     A downgrade (e.g. 0.9.13 -> 0.9.12) fails the check.

  2. If the version has been bumped, ``CHANGELOG.md`` must contain a heading
     for the new version (``## [x.y.z]``).

Exits 0 on success, 1 on failure. Intended to run in CI on pull requests.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import tomllib
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_PY = REPO_ROOT / "src" / "agentcache" / "__init__.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

_INIT_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def _pyproject_version(text: str) -> str:
    return tomllib.loads(text)["project"]["version"]


def _init_version(text: str) -> str:
    match = _INIT_VERSION_RE.search(text)
    if not match:
        raise ValueError("Could not find __version__ in __init__.py")
    return match.group(1)


def _git_show(ref: str, path: Path) -> str | None:
    rel = path.relative_to(REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _parse(version: str, label: str) -> Version:
    try:
        return Version(version)
    except InvalidVersion as exc:
        raise SystemExit(f"[version-guard] invalid version in {label}: {exc}")


def _changelog_has_heading(version: str) -> bool:
    if not CHANGELOG.exists():
        return False
    pattern = re.compile(rf"^##\s*\[{re.escape(version)}\]", re.MULTILINE)
    return bool(pattern.search(CHANGELOG.read_text(encoding="utf-8")))


def main(base_ref: str) -> int:
    current_pyproject = _parse(
        _pyproject_version(PYPROJECT.read_text(encoding="utf-8")),
        "pyproject.toml",
    )
    current_init = _parse(
        _init_version(INIT_PY.read_text(encoding="utf-8")),
        "src/agentcache/__init__.py",
    )

    failures: list[str] = []

    if current_pyproject != current_init:
        failures.append(
            f"pyproject.toml version ({current_pyproject}) does not match "
            f"src/agentcache/__init__.py version ({current_init})"
        )

    base_pyproject_text = _git_show(base_ref, PYPROJECT)
    if base_pyproject_text is None:
        print(
            f"[version-guard] base ref '{base_ref}' unavailable; "
            "skipping regression check",
            file=sys.stderr,
        )
    else:
        base_version = _parse(
            _pyproject_version(base_pyproject_text),
            f"{base_ref}:pyproject.toml",
        )
        if current_pyproject < base_version:
            failures.append(
                f"version regression: pyproject.toml is {current_pyproject} "
                f"but {base_ref} is {base_version}. Refusing to publish a "
                "lower version than what is already on the base branch."
            )
        if current_pyproject > base_version and not _changelog_has_heading(
            str(current_pyproject)
        ):
            failures.append(
                f"CHANGELOG.md is missing a '## [{current_pyproject}]' heading "
                f"for the bumped version (base was {base_version})."
            )

    if failures:
        for msg in failures:
            print(f"[version-guard] FAIL: {msg}", file=sys.stderr)
        return 1

    print(f"[version-guard] OK: version={current_pyproject}")
    return 0


if __name__ == "__main__":
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    sys.exit(main(ref))
