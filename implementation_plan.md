# Implementation Plan - Python Package Library Support

This plan details the technical steps to transform **agentcache** from a standalone service daemon into a reusable Python library. This enables any developer to import `AgentCache` or run their own custom programmatic instances inside their Python scripts.

---

## Proposed Changes

### Library Core Module

#### [NEW] [core.py](file:///D:/Downloads/Projects/Other%20Projects/agentcache/src/agentcache/core.py)
Create a new core module defining the OOP classes:
1. **`AgentCacheConfig`**: A configuration dataclass carrying settings like `max_obs_per_folder`, `token_budget`, and fallback environment variables.
2. **`AgentCache`**: The high-level developer facade wrapping `StateKV` database storage and `functions.py` capabilities.
   - It will manage its own database file connection pool.
   - It will manage its own instance-level `BM25Index` and `VectorIndex` to prevent cross-talk when multiple cache instances are run in the same process.
   - It will provide standard object methods: `observe()`, `search()`, `remember()`, `forget()`, `get_timeline()`, `get_graph()`, `health_check()`.
3. **`AgentCacheServer`**: A programmatic wrapper to start/stop the Flask + WebSocket REST/MCP server daemon.
   - Supports non-blocking startup in a background thread or subprocess.

### Package Entrypoint

#### [MODIFY] [__init__.py](file:///D:/Downloads/Projects/Other%20Projects/agentcache/src/agentcache/__init__.py)
* Import and expose `AgentCache`, `AgentCacheServer`, and `AgentCacheConfig` at the package root level:
  ```python
  from .core import AgentCache, AgentCacheServer, AgentCacheConfig
  ```

### Test Suite

#### [NEW] [test_library.py](file:///D:/Downloads/Projects/Other%20Projects/agentcache/tests/test_library.py)
* Implement comprehensive library-mode unit tests:
  * **Isolation Check**: Initialize two distinct `AgentCache` instances (`cache_a` and `cache_b`) pointing to separate temporary SQLite databases. Verify that observations logged in `cache_a` are not visible or searchable in `cache_b`.
  * **Memory Verification**: Verify that long-term memories are properly versioned and recalled independently on a per-cache-instance basis.
  * **Server Startup Check**: Programmatically start a test `AgentCacheServer` instance on a random open port, query its `/livez` health endpoint to verify it is responsive, and then shut it down gracefully.

---

## Verification Plan

### Automated Tests
Run the standard pytest test suite, including the new library unit tests:
```bash
pytest tests/ -v
```

Ensure the Ruff formatter check runs successfully:
```bash
python -m ruff format --check src/ tests/
python -m ruff check src/ tests/
```
