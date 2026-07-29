"""Integration tests for the ``agentcache`` CLI.

Seam under test: ``python -m agentcache.cli`` invoked as a real subprocess.
No mocking of argparse, no calling ``main()`` in-process — the failure mode
that motivated this suite (regressing ``--version`` after a stale merge) only
shows up when the whole CLI actually runs the way users invoke it.

Uses ``sys.executable -m agentcache.cli`` so the tests exercise the exact
Python that pytest is running under, avoiding PATH surprises across CI OSes.
"""

from __future__ import annotations

import re
import subprocess
import sys

import agentcache


def _run_cli(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
    """Invoke the CLI as a subprocess and return the completed process.

    ``check=False`` so tests can assert on the exit code themselves.
    """
    return subprocess.run(
        [sys.executable, "-m", "agentcache.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_version_flag_prints_installed_version_and_exits_zero():
    """``agentcache --version`` must print exactly the installed version.

    Regression guard for #61 (and yesterday's near-miss where a stale-branch
    merge could have downgraded the version). If ``__version__`` and what the
    CLI prints ever diverge, this fails.
    """
    result = _run_cli("--version")

    assert result.returncode == 0, (
        f"Expected exit 0 for --version, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    # argparse's ``action="version"`` writes to stdout on Python 3.4+.
    # Assert on the version line specifically — the import currently emits a
    # ``[config] Loaded environment ...`` banner ahead of it, which is UX
    # noise but not a --version regression per se.
    expected_line = f"agentcache {agentcache.__version__}"
    combined_lines = (result.stdout + result.stderr).splitlines()
    assert expected_line in combined_lines, (
        f"Expected a line {expected_line!r} in CLI output, "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_help_lists_every_subcommand_and_exits_zero():
    """``agentcache --help`` must exit 0 and mention every subcommand.

    If someone adds a subcommand and forgets to register it, or removes one
    without cleaning up the dispatch table, this fails.
    """
    result = _run_cli("--help")

    assert result.returncode == 0
    stdout = result.stdout
    # Every subcommand the CLI dispatches must appear as a whole word in help
    # output — substring matching would let ``serve``->``serves`` slip past.
    for subcommand in ("serve", "worker", "migrate", "export", "context", "connect"):
        pattern = rf"\b{re.escape(subcommand)}\b"
        assert re.search(pattern, stdout), (
            f"Expected subcommand {subcommand!r} as a whole word in --help "
            f"output. Either the subparser was removed/renamed or --help is "
            f"broken. stdout={stdout!r}"
        )


def test_no_subcommand_fails_with_nonzero_exit():
    """Running ``agentcache`` with no subcommand must fail loudly.

    argparse marks ``subparsers`` as ``required=True`` — this test locks that
    down so nobody 'helpfully' relaxes it and silently no-ops.
    """
    result = _run_cli()

    assert result.returncode != 0, (
        "Expected non-zero exit when no subcommand given, got 0. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # argparse writes the "required" error to stderr.
    combined = (result.stdout + result.stderr).lower()
    assert "required" in combined or "command" in combined, (
        f"Expected a 'required'/'command' hint in error output, "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_unknown_flag_fails_with_nonzero_exit():
    """Unknown flags must be rejected, not silently ignored."""
    result = _run_cli("--nonsense-flag-that-does-not-exist")

    assert result.returncode != 0
    assert "unrecognized" in result.stderr.lower() or "error" in result.stderr.lower()
