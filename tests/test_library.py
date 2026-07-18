"""
tests/test_library.py

Unit tests for AgentCache library OOP wrapper interfaces (AgentCache & AgentCacheServer).
"""

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcache import AgentCache, AgentCacheServer


def test_agent_cache_isolation(tmp_path):
    """Verify that multiple AgentCache instances in the same process remain fully isolated in database and index layers."""
    db_a = str(tmp_path / "cache_a.db")
    db_b = str(tmp_path / "cache_b.db")

    cache_a = AgentCache(db_path=db_a, max_obs_per_folder=10)
    cache_b = AgentCache(db_path=db_b, max_obs_per_folder=10)

    # 1. Log observation in cache_a
    cache_a.observe(
        folder_path="projects/app", agent_id="agent-a", content="Secret alpha note"
    )

    # 2. Log observation in cache_b
    cache_b.observe(
        folder_path="projects/app", agent_id="agent-b", content="Secret beta note"
    )

    # 3. Verify search isolation
    res_a = cache_a.search("alpha", folder_path="projects/app")
    assert len(res_a) == 1
    assert "alpha" in res_a[0]["text"].lower()

    res_b_on_a = cache_b.search("alpha", folder_path="projects/app")
    assert len(res_b_on_a) == 0  # Should not find A's note in B

    res_b = cache_b.search("beta", folder_path="projects/app")
    assert len(res_b) == 1
    assert "beta" in res_b[0]["text"].lower()


def test_agent_cache_memory_versioning(tmp_path):
    """Verify global long-term memory operations and Jaccard similarity versioning works independently."""
    db_file = str(tmp_path / "mem_test.db")
    cache = AgentCache(db_path=db_file)

    # Add insight
    mem1 = cache.remember(
        title="Python Optimization", content="Use generators to save RAM memory"
    )
    assert mem1["memory"]["id"] is not None

    # Add superceding insight (> 0.7 Jaccard match)
    cache.remember(
        title="Python Optimization",
        content="Use generators to save RAM memory mostly",
    )

    # Retrieve memories and check versioning
    memories = cache.search("generators")
    assert len(memories) >= 1
    # Check latest flag or content
    latest = [m for m in memories if m.get("isLatest") is not False]
    assert len(latest) == 1
    assert "mostly" in latest[0]["content"]


def _get_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_agent_cache_server_lifecycle(tmp_path):
    """Verify that AgentCacheServer programmatically starts, responds, and terminates gracefully."""
    db_file = str(tmp_path / "server_test.db")

    port = _get_free_port()
    server = AgentCacheServer(port=port, db_path=db_file)

    # Start in background and wait deterministically for boot readiness
    server.start(background=True, wait=True, timeout=60.0)

    try:
        # Check health endpoint
        url = f"http://127.0.0.1:{port}/agentcache/livez"
        response = requests.get(url, timeout=5.0)

        assert response.status_code == 200, (
            f"Unexpected status: {response.status_code}, body={response.text}"
        )
        assert response.json()["status"] == "ok"
    finally:
        # Shutdown server configurations
        server.stop()
