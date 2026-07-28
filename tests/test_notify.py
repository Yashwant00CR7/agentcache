"""Tests for plugin/scripts/notify.py — #49 (Save/Skip confirm UX).

confirm_flush returns True to proceed with the flush, False only on an explicit
Skip. Every fallback path (disabled, unsupported platform, missing notifier,
error) must return True and never raise.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugin" / "scripts"


@pytest.fixture
def notify(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    if "notify" in sys.modules:
        del sys.modules["notify"]
    return importlib.import_module("notify")


class _Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout


def test_disabled_proceeds_without_prompting(notify, monkeypatch):
    monkeypatch.delenv("AGENTCACHE_FLUSH_CONFIRM", raising=False)

    called = []
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: called.append(a))

    assert notify.confirm_flush("proj") is True  # proceed
    assert called == []  # no prompt dispatched when disabled


def test_macos_skip_suppresses_flush(notify, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_FLUSH_CONFIRM", "true")
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        notify.subprocess,
        "run",
        lambda *a, **k: _Proc("button returned:Skip, gave up:false"),
    )

    assert notify.confirm_flush("myproj") is False  # Skip → suppress


def test_macos_save_proceeds(notify, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_FLUSH_CONFIRM", "1")
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)

    seen = []

    def _run(*a, **k):
        seen.append(a[0])
        return _Proc("button returned:Save, gave up:false")

    monkeypatch.setattr(notify.subprocess, "run", _run)

    assert notify.confirm_flush("myproj") is True  # Save → proceed
    # Project label is threaded into the dialog script.
    assert any("myproj" in " ".join(argv) for argv in seen)


def test_macos_missing_osascript_falls_back_to_proceed(notify, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_FLUSH_CONFIRM", "true")
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)

    called = []
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: called.append(a))

    assert notify.confirm_flush("proj") is True  # no dialog → silent proceed
    assert called == []


def test_macos_error_never_blocks(notify, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_FLUSH_CONFIRM", "true")
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=35)

    monkeypatch.setattr(notify.subprocess, "run", _boom)

    assert notify.confirm_flush("proj") is True  # error → fall back to proceed


def test_linux_defaults_to_proceed(notify, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_FLUSH_CONFIRM", "true")
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: _Proc())

    assert notify.confirm_flush("proj") is True  # notify-send can't capture Skip
