"""Tests for plugin/scripts/hook_utils.py — #44 (fail loudly, not silently)."""

import importlib
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugin" / "scripts"


@pytest.fixture
def hook_utils(monkeypatch, tmp_path):
    """Import hook_utils with the scripts dir on sys.path and a temp log file."""
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    log_file = tmp_path / "hooks.log"
    monkeypatch.setenv("AGENTCACHE_HOOK_LOG", str(log_file))
    if "hook_utils" in sys.modules:
        del sys.modules["hook_utils"]
    module = importlib.import_module("hook_utils")
    module._log_file = log_file
    return module


def _read_log(module):
    p = Path(module._hook_log_path())
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_api_call_logs_http_error_distinctly(hook_utils, monkeypatch):
    def _raise_http(*_a, **_k):
        raise urllib.error.HTTPError(
            url="http://x/agentmemory/context",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(hook_utils.urllib.request, "urlopen", _raise_http)

    result = hook_utils.api_call("context", {"a": 1})
    assert result is None  # signature/behaviour unchanged

    log = _read_log(hook_utils)
    assert "http_error" in log
    assert "404" in log
    assert "context" in log


def test_api_call_logs_network_error_distinctly(hook_utils, monkeypatch):
    def _raise_url(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(hook_utils.urllib.request, "urlopen", _raise_url)

    result = hook_utils.api_call("summarize", {"a": 1})
    assert result is None

    log = _read_log(hook_utils)
    assert "network_error" in log
    assert "http_error" not in log


def test_api_call_success_writes_no_error_log(hook_utils, monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(hook_utils.urllib.request, "urlopen", lambda *_a, **_k: _Resp())

    result = hook_utils.api_call("context", {"a": 1})
    assert result == {"ok": True}
    assert _read_log(hook_utils) == ""


def test_api_call_bg_is_fire_and_forget(hook_utils, monkeypatch):
    # api_call_bg must not raise even when the underlying call errors.
    def _raise_http(*_a, **_k):
        raise urllib.error.HTTPError("http://x", 500, "boom", None, None)

    monkeypatch.setattr(hook_utils.urllib.request, "urlopen", _raise_http)
    hook_utils.api_call_bg("context", {"a": 1})  # returns immediately, no raise
