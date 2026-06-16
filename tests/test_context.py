"""
tests/test_context.py — C1.4

Tests for context(), export_data(), and token budget enforcement.
"""
import sys
import os
import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kv(tmp_path):
    from db import StateKV
    os.environ.pop("AGENTCACHE_SECRET", None)
    return StateKV(db_path=str(tmp_path / "test.db"))


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------------------------
# context() tests
# ---------------------------------------------------------------------------

class TestContext:
    def test_empty_db_returns_minimal_context(self, tmp_path):
        """Empty DB should return a well-formed but empty context."""
        from functions import context
        kv = _make_kv(tmp_path)
        result = context(kv, {
            "sessionId": "sess_test_empty",
            "project": "/home/user/my-project",
            "budget": 2000,
        })
        assert isinstance(result, dict)
        assert "context" in result
        assert "blocks" in result
        assert "tokens" in result
        assert result["blocks"] == 0
        assert result["tokens"] == 0

    def test_raises_on_missing_session_id(self, tmp_path):
        from functions import context
        kv = _make_kv(tmp_path)
        with pytest.raises(ValueError):
            context(kv, {"project": "/home/user/proj"})

    def test_raises_on_missing_project(self, tmp_path):
        from functions import context
        kv = _make_kv(tmp_path)
        with pytest.raises(ValueError):
            context(kv, {"sessionId": "sess_x"})

    def test_respects_token_budget(self, tmp_path):
        """Context output tokens should not exceed the requested budget."""
        from functions import context, lesson_save, KV
        kv = _make_kv(tmp_path)
        project = "/home/user/budget-test"

        # Add many lessons to push towards the budget
        for i in range(20):
            lesson_save(kv, {
                "content": f"Lesson {i}: " + ("x " * 100),
                "project": project,
                "confidence": 0.9,
            })

        budget = 500
        result = context(kv, {
            "sessionId": "sess_budget",
            "project": project,
            "budget": budget,
        })
        # Token estimate is len/3 — check that total tokens respects budget
        assert result["tokens"] <= budget + 50  # small headroom for header/footer

    def test_context_includes_xml_wrapper(self, tmp_path):
        """Non-empty context should be wrapped in <agentcache-context>."""
        from functions import context, lesson_save
        kv = _make_kv(tmp_path)
        project = "/home/user/xml-test"

        lesson_save(kv, {
            "content": "Always validate user input before processing",
            "project": project,
            "confidence": 0.8,
        })

        result = context(kv, {
            "sessionId": "sess_xml",
            "project": project,
            "budget": 2000,
        })

        if result["blocks"] > 0:
            assert "<agentcache-context" in result["context"]
            assert "</agentcache-context>" in result["context"]

    def test_token_budget_env_var_respected(self, tmp_path, monkeypatch):
        """TOKEN_BUDGET env var should be used when no budget param given."""
        from functions import context, lesson_save
        kv = _make_kv(tmp_path)
        project = "/home/user/env-budget"
        monkeypatch.setenv("TOKEN_BUDGET", "100")

        for i in range(10):
            lesson_save(kv, {
                "content": f"Important lesson {i}: " + ("word " * 50),
                "project": project,
                "confidence": 0.9,
            })

        result = context(kv, {"sessionId": "sess_env_budget", "project": project})
        # Should use TOKEN_BUDGET=100 from env
        assert result["tokens"] <= 150  # with some headroom for XML wrapper


# ---------------------------------------------------------------------------
# export_data() tests
# ---------------------------------------------------------------------------

class TestExportData:
    def test_export_returns_folders_and_memories(self, tmp_path):
        from functions import folder_observe, remember, export_data
        kv = _make_kv(tmp_path)

        folder_observe(kv, {
            "folderPath": "/home/user/export-test",
            "agentId": "kiro",
            "text": "Working on export feature",
            "timestamp": _now(),
        })
        remember(kv, {"content": "Export data uses v2 format"})

        result = export_data(kv, {})
        assert isinstance(result, dict)
        assert "folders" in result or "observations" in result or "memories" in result

    def test_export_empty_db(self, tmp_path):
        from functions import export_data
        kv = _make_kv(tmp_path)
        result = export_data(kv, {})
        assert isinstance(result, dict)
        # Should not crash on empty DB


# ---------------------------------------------------------------------------
# estimate_tokens()
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        from functions import estimate_tokens
        assert estimate_tokens("") == 0

    def test_typical_text(self):
        from functions import estimate_tokens
        text = "hello world this is a test" * 10
        tokens = estimate_tokens(text)
        # Should be approximately len/3
        assert tokens == len(text) // 3
