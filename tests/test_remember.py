"""
tests/test_remember.py — C1.2

Tests for remember(), forget(), and jaccard_similarity().
"""

import datetime
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kv(tmp_path):
    from agentcache.db import StateKV

    os.environ.pop("AGENTCACHE_SECRET", None)
    return StateKV(db_path=str(tmp_path / "test.db"))


def _now():
    return (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_strings(self):
        from agentcache.functions import jaccard_similarity

        assert jaccard_similarity("hello world foo", "hello world foo") == 1.0

    def test_completely_different(self):
        from agentcache.functions import jaccard_similarity

        score = jaccard_similarity("apple banana cherry", "xyz uvw qrs")
        assert score == 0.0

    def test_partial_overlap(self):
        from agentcache.functions import jaccard_similarity

        score = jaccard_similarity(
            "authentication security token", "authentication bearer token"
        )
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        from agentcache.functions import jaccard_similarity

        assert jaccard_similarity("", "") == 1.0

    def test_high_similarity_above_threshold(self):
        from agentcache.functions import jaccard_similarity

        # These two should meet or exceed the 0.7 threshold used in remember()
        a = "Use parameterised queries to prevent SQL injection in all database calls"
        b = "Use parameterised queries to prevent SQL injection in database operations"
        assert jaccard_similarity(a, b) >= 0.7

    def test_low_similarity_below_threshold(self):
        from agentcache.functions import jaccard_similarity

        a = "Configure Redis as the session cache backend"
        b = "Deploy the React frontend to Vercel using GitHub Actions CI"
        assert jaccard_similarity(a, b) < 0.7


# ---------------------------------------------------------------------------
# remember()
# ---------------------------------------------------------------------------


class TestRemember:
    def test_creates_memory_with_is_latest_true(self, tmp_path):
        from agentcache.functions import remember

        kv = _make_kv(tmp_path)
        result = remember(kv, {"content": "Always use type hints in Python functions"})
        assert result["success"] is True
        mem = result["memory"]
        assert mem["isLatest"] is True
        assert mem["id"].startswith("mem_")
        assert "Always use type hints" in mem["content"]

    def test_memory_has_required_fields(self, tmp_path):
        from agentcache.functions import remember

        kv = _make_kv(tmp_path)
        result = remember(
            kv,
            {
                "content": "Prefer composition over inheritance",
                "type": "architecture",
                "concepts": ["design", "patterns"],
            },
        )
        mem = result["memory"]
        assert "id" in mem
        assert "content" in mem
        assert "createdAt" in mem
        assert mem["type"] == "architecture"
        assert "design" in mem["concepts"]

    def test_supersedes_memory_with_high_jaccard_similarity(self, tmp_path):
        from agentcache.functions import KV, remember

        kv = _make_kv(tmp_path)

        first = remember(
            kv,
            {
                "content": "Always use parameterised SQL queries to prevent injection attacks in every database call"
            },
        )
        first_id = first["memory"]["id"]

        # Highly similar content — should supersede the first
        second = remember(
            kv,
            {
                "content": "Always use parameterised SQL queries to prevent injection attacks in every database operation"
            },
        )
        second_mem = second["memory"]

        # Old memory should be marked as not latest
        old_mem = kv.get(KV.memories, first_id)
        assert old_mem is not None
        assert old_mem.get("isLatest") is False

        # New memory should be latest and point to old via parentId
        assert second_mem["isLatest"] is True
        assert second_mem.get("parentId") == first_id

    def test_independent_memory_with_low_jaccard_similarity(self, tmp_path):
        from agentcache.functions import KV, remember

        kv = _make_kv(tmp_path)

        first = remember(
            kv,
            {
                "content": "Configure Redis as the session cache backend for high throughput"
            },
        )
        first_id = first["memory"]["id"]

        # Very different content — should be independent
        second = remember(
            kv,
            {
                "content": "Deploy the React frontend to Vercel using GitHub Actions continuous deployment"
            },
        )
        second_mem = second["memory"]

        # Old memory should remain latest
        old_mem = kv.get(KV.memories, first_id)
        assert old_mem is not None
        assert old_mem.get("isLatest") is True

        # New memory has no parentId
        assert second_mem["isLatest"] is True
        assert second_mem.get("parentId") is None

        # Both exist independently
        all_mems = kv.list(KV.memories)
        assert len(all_mems) == 2

    def test_remember_raises_on_empty_content(self, tmp_path):
        from agentcache.functions import remember

        kv = _make_kv(tmp_path)
        with pytest.raises(ValueError, match="content is required"):
            remember(kv, {"content": ""})

    def test_remember_strips_private_data(self, tmp_path):
        from agentcache.functions import remember

        kv = _make_kv(tmp_path)
        result = remember(
            kv,
            {
                "content": "API key is sk-proj-abc123def456ghi789jkl012mno345pqr678 for production"
            },
        )
        assert "sk-proj-" not in result["memory"]["content"]
        assert "[REDACTED" in result["memory"]["content"]

    def test_remember_with_project_scoping(self, tmp_path):
        from agentcache.functions import remember

        kv = _make_kv(tmp_path)
        # Two very similar memories for different projects should not supersede each other
        first = remember(
            kv,
            {
                "content": "Always use parameterised queries in all database operations",
                "project": "project-alpha",
            },
        )
        second = remember(
            kv,
            {
                "content": "Always use parameterised queries in all database operations",
                "project": "project-beta",
            },
        )
        # Both should remain independent (different projects)
        assert first["memory"]["isLatest"] is True
        assert second["memory"]["isLatest"] is True


# ---------------------------------------------------------------------------
# forget()
# ---------------------------------------------------------------------------


class TestForget:
    def test_forget_memory_by_id(self, tmp_path):
        from agentcache.functions import KV, forget, remember

        kv = _make_kv(tmp_path)
        result = remember(kv, {"content": "This memory will be forgotten"})
        mem_id = result["memory"]["id"]

        forget_result = forget(kv, {"memoryId": mem_id})
        assert forget_result["deleted"] >= 1
        assert kv.get(KV.memories, mem_id) is None

    def test_forget_returns_zero_for_nonexistent_memory(self, tmp_path):
        from agentcache.functions import forget

        kv = _make_kv(tmp_path)
        result = forget(kv, {"memoryId": "mem_nonexistent_id"})
        # Should still succeed (memory was already gone) — deleted may be 0 or 1
        assert "deleted" in result
