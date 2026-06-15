# Technical Issues, Bugs, & Architecture Audit: agentmemory-python

This document details all technical anomalies, display bugs, synchronization issues, and functional mismatches discovered during the code audit of the `agentmemory-python` project.

---

## Category A: Syntax & Technical Code Correctness

### 1. SQLite Parameterization Syntax Error (`%s` vs `?`)
* **File & Lines:** [src/functions.py:L411-429](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L411-L429) (within `IndexPersistence.save_sharded_index`)
* **The Code:**
  ```python
  cursor.execute(
      "SELECT DISTINCT scope FROM kv_store WHERE scope LIKE %s",
      (scope_prefix + "%",)
  )
  ...
  format_strings = ','.join(['%s'] * len(chunk_delete))
  cursor.execute(
      f"DELETE FROM kv_store WHERE scope IN ({format_strings})",
      tuple(chunk_delete)
  )
  ```
* **Root Cause:** The database backend uses standard Python `sqlite3`, which expects `?` placeholders for parameter injection. `%s` is specific to MySQL or PostgreSQL clients.
* **Consequence:** Executing `save_sharded_index` throws a `sqlite3.OperationalError` every time it attempts to clean up old sharded indexes. This error is captured in a silent `try-except` block, meaning old shards are **never** deleted, leading to database bloat.
* **Remediation:** Replace `%s` with `?` in both queries:
  ```python
  cursor.execute(
      "SELECT DISTINCT scope FROM kv_store WHERE scope LIKE ?",
      (scope_prefix + "%",)
  )
  ...
  format_strings = ','.join(['?'] * len(chunk_delete))
  ```

### 2. Thread Safety Write Locks Omitted
* **File & Lines:** [src/db.py:L75-105](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/db.py#L75-L105) (within `StateKV.set` and `StateKV.delete`)
* **The Code:** The `set()`, `delete()`, and `commit_version()` write methods execute queries and transactions directly on SQLite connections without acquiring `self._lock`. Only `update()` acquires the lock:
  ```python
  def update(self, scope: str, key: str, ops: List[Dict[str, Any]]) -> Optional[Any]:
      with self._lock:
          ...
  ```
* **Root Cause:** SQLite operates database-level write serialization. In a multi-threaded Flask environment, two threads making concurrent database modifications (one inside `update` and another executing a basic `set` or `delete`) can cause a database race condition.
* **Consequence:** Occasional `sqlite3.OperationalError: database is locked` exceptions, transaction rollbacks, or lost writes.
* **Remediation:** Wrap the database write operations in `set()`, `delete()`, and `commit_version()` with `with self._lock:`.

### 3. Nonexistent Delete Memory API Endpoint Called
* **File & Lines:** [src/viewer/index.html:L2813](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L2813) (within `confirmDeleteMemory`)
* **The Code:**
  ```javascript
  await apiDelete('governance/memories', { memoryIds: [id], reason: 'Deleted via viewer' });
  ```
* **Root Cause:** The backend [src/app.py](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/app.py) contains no `/agentmemory/governance/memories` endpoint. It processes deletions via a `POST` route at `/agentmemory/forget`, which expects a payload of `{"memoryId": "<id>"}`.
* **Consequence:** The delete memory request fails with an HTTP 404 / 405 error, leaving memories undeletable from the UI.
* **Remediation:** Update `confirmDeleteMemory` to call `apiPost` on the correct path and query structure:
  ```javascript
  await apiPost('forget', { memoryId: id });
  ```

### 4. Nonexistent Graph Modification Endpoints Called
* **File & Lines:** [src/viewer/index.html:L2292](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L2292) and [L2404](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L2404)
* **The Code:**
  ```javascript
  // L2292 (expanding neighbors)
  var result = await apiPost('graph/query', { startNodeId: nodeId, maxDepth: 1 });
  ...
  // L2404 (rebuilding graph)
  await apiPost('graph/build', {});
  ```
* **Root Cause:** The backend lacks routes for `/graph/query` and `/graph/build`. It only implements a read-only endpoint `/agentmemory/graph/stats`.
* **Consequence:** Interactive graph expansion and manual rebuilding fail with HTTP 404 errors in the console.
* **Remediation:** Register endpoints `/agentmemory/graph/query` and `/agentmemory/graph/build` in `app.py` or remove these interactive buttons from the dashboard sidebar if they are not supported by the Python backend.

---

## Category B: Functional Alignment & Purpose Verification

### 1. Project-Scoped Memory Slots Collapsing into Global Scope
* **File & Lines:** [src/functions.py:L1277](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L1277) (within `slot_create`) and [L1216](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L1216) (within `list_pinned_slots`)
* **The Code:**
  ```python
  # L1277
  target_kv = KV.globalSlots if scope == "global" else KV.slots
  ...
  # L1216
  p_slots = kv.list(KV.slots)
  ```
* **Root Cause:** The KV namespace for project-scoped slots is hardcoded as `KV.slots = "mem:slots"`. This is a single, shared global namespace for all projects.
* **Consequence:** Project scoping is broken. Any project-scoped slot (e.g. `current_task` or `project_context`) is saved under the global `mem:slots` namespace. If Project A writes to `current_task`, it overwrites Project B's `current_task`. Furthermore, Project B inherits Project A's slots, violating intended project isolation boundaries.
* **Remediation:** Derive the project slots database scope dynamically by incorporating the project ID or path name. For example:
  ```python
  # In functions.py, scope project slots by project name
  def project_slots_scope(project: str) -> str:
      return f"mem:slots:{project}"
  ```
  And update `slot_create`, `slot_append`, `slot_replace`, `slot_delete`, and `context` retrieval to pass the project name and use this dynamic scope prefix.

### 2. Aggressive Auto-Completion of Parallel Sessions
* **File & Lines:** [src/functions.py:L117-131](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L117-L131) (within `auto_complete_old_active_sessions`)
* **The Code:**
  ```python
  def auto_complete_old_active_sessions(kv: StateKV, current_session_id: str) -> int:
      sessions = kv.list(KV.sessions)
      count = 0
      for s in sessions:
          if s.get("id") != current_session_id and s.get("status") == "active":
              s["status"] = "completed"
              ...
              kv.set(KV.sessions, s["id"], s)
  ```
* **Root Cause:** The system operates under the assumption that only one agent session can be active globally. Starting a session or recording an observation automatically marks all other active sessions as completed.
* **Consequence:** If multiple workspaces, parallel subagents, or agents operate concurrently using the same backend, they force-close each other's sessions. Observations are then recorded against closed sessions, or new sessions are spawned, constantly closing others.
* **Remediation:** Scour out the global session force-completion. Sessions should only be closed:
  - Explicitly via the `/session/end` endpoint
  - When matching the exact project or agent scope (e.g., only auto-complete active sessions belonging to the same project or the same `agentId`).

### 3. Agent Scoping Bypassed in MCP Tools
* **File & Lines:** [src/app.py:L2200-2226](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/app.py#L2200-L2226) (within `memory_sessions` and `memory_observations` MCP handlers)
* **The Code:** The REST routes respect `AGENTMEMORY_AGENT_SCOPE=isolated` to restrict session/observation lookups to the configured `agentId` (via `functions.is_agent_scope_isolated()`). However, the MCP tool handlers fetch data globally:
  ```python
  # L2206 - MCP memory_sessions handler
  sessions = functions.list_sessions(kv)  # Fetches all sessions globally
  ```
* **Root Cause:** The MCP dispatch logic calls the database list functions directly without checking or applying the agent isolation filters.
* **Consequence:** An agent running in an isolated environment (`AGENTMEMORY_AGENT_SCOPE=isolated`) can query other agents' sessions and observations via the MCP tools, violating the promised security isolation boundary.
* **Remediation:** Apply `is_agent_scope_isolated()` and `get_agent_id()` filters inside the MCP dispatcher cases for `memory_sessions` and `memory_observations`.

### 4. Telemetry Events and Gauges Legacy Debt
* **File & Lines:** [src/viewer/index.html:L1596-1637](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L1596-L1637) (within `renderDashboard`)
* **Root Cause:** The dashboard UI has gauge components for CPU, Heap memory, RSS memory, Event loop lag, and Circuit Breaker states, which are legacy features from the Node.js implementation. The Python Flask server returns a minimal hardcoded health structure:
  ```python
  # functions.py L2177
  return {
      "status": "healthy" if db_status == "connected" else "degraded",
      "service": "agentmemory",
      "version": "0.9.8",
      "database": "dolt", # Legacy name, database is actually sqlite
      "databaseStatus": db_status
  }
  ```
* **Consequence:** The gauges show empty, fallback, or zero values in the dashboard. Additionally, the database type displays as `"dolt"`, which is legacy debt (the project is now SQLite).
* **Remediation:** 
  - Update `health_check` in [src/functions.py](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L2170) to return `"database": "sqlite"`.
  - To support the UI gauges, implement basic process metric collection in Python (e.g. using `os` or `resource` packages) to return CPU and Memory stats, or simplify the dashboard UI to omit Node-specific gauges.

---

## Category C: Real-Time Data Synchronization

### 1. Consolidated Memories Bypassing Search Indexes
* **File & Lines:** [src/functions.py:L2512](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L2512) and [L2532](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L2532) (within `consolidate` memory saving loops)
* **The Code:**
  ```python
  kv.set(KV.memories, evolved["id"], evolved)
  ...
  kv.set(KV.memories, memory["id"], memory)
  ```
* **Root Cause:** When memories are merged or consolidated, they are saved directly to the SQLite database via `kv.set()`, but they are **never** added to the BM25 index (`_bm25_index.add()`) or the Vector index (`vector_index_add_guarded()`).
* **Consequence:** Consolidated memories remain search-invisible. They are omitted from search results (`memory_recall`, `memory_smart_search`) and context compilation until the server is rebooted (rebooting forces index rebuild from the DB).
* **Remediation:** Add indexing commands directly after saving the memory in `consolidate`:
  ```python
  # Index evolved memory
  try:
      _bm25_index.add(memory_to_observation(evolved))
  except Exception:
      pass
  comb_text = evolved["title"] + " " + evolved["content"]
  vector_index_add_guarded(evolved["id"], "memory", comb_text, {"kind": "memory", "logId": evolved["id"]})
  ```
  Repeat this for the newly created `memory` object on line 2532.

### 2. Raw Observations Overwritten by Compressed Observations
* **File & Lines:** [src/functions.py:L719](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L719) and [L770](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/functions.py#L770) (within `observe`)
* **The Code:**
  ```python
  # L719 - Save raw observation
  kv.set(KV.observations(session_id), obs_id, raw)
  ...
  # L770 - Overwrite with synthetic/compressed observation
  kv.set(KV.observations(session_id), obs_id, synthetic)
  ```
* **Root Cause:** Both calls write to the exact same scope (`KV.observations(session_id)`) and key (`obs_id`).
* **Consequence:** The raw observation is completely overwritten. The backend endpoints and APIs designed to fetch "raw" vs. "compressed" observations are redundant, as only the compressed version is stored (with raw inputs nested inside the `raw` field).
* **Remediation:** Store raw observations under a separate database key or suffix (e.g. `f"{obs_id}:raw"`), or store them in a distinct scope (e.g. `KV.rawObservations(session_id)`).

---

## Category D: Frontend Page Connection & Tab Synchronization

### 1. Silent WebSocket Message Discard (Payload Shape Mismatch)
* **File & Lines:** [src/viewer/index.html:L4130](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L4130) (within `ws.onmessage` event listener)
* **The Code:**
  ```javascript
  ws.onmessage = function(e) {
    if (state.ws !== ws) return;
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'stream' && msg.event) {
        handleStreamEvent(msg);
      } else if (msg.event_type && msg.data) {
        handleStreamEvent({ event: { type: 'create', data: msg.data, event_type: msg.event_type } });
      }
    } catch {}
  };
  ```
* **Root Cause:** The Python backend broadcasts observations in the following structure:
  - `{"type": "raw_observation", "sessionId": ..., "data": {"type": "raw", "observation": {...}}}`
  - `{"type": "compressed_observation", "sessionId": ..., "data": {"type": "compressed", "observation": {...}}}`
  Neither has `msg.type === 'stream'` or `msg.event_type`.
* **Consequence:** The WebSocket parser silently ignores all server-sent broadcasts. Real-time logging of new observations or commands is broken.
* **Remediation:** Add handlers for Python's `raw_observation` and `compressed_observation` messages:
  ```javascript
  if (msg.type === 'raw_observation' || msg.type === 'compressed_observation') {
    if (msg.data && msg.data.observation) {
      routeWsMessage({ observation: msg.data.observation });
    }
  } else if (msg.type === 'stream' && msg.event) {
     ...
  ```

### 2. WebSocket Connection Disabling Fallback Polling
* **File & Lines:** [src/viewer/index.html:L4125](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L4125) (within `ws.onopen`)
* **Root Cause:** When the WebSocket connection succeeds, the frontend calls `stopPolling()`.
* **Consequence:** Because WebSocket events are silently discarded due to the payload mismatch, and polling is stopped, the dashboard ceases to update at all once the WebSocket is connected. The UI remains completely frozen.
* **Remediation:** Fixing the WebSocket event parsing (Issue D1) will restore live updates. Additionally, if the WebSocket disconnects, the polling mechanism must be correctly re-triggered.

### 3. Cached Pages/Tabs Preventing Sync on Navigation
* **File & Lines:** [src/viewer/index.html:L1502-1518](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L1502-1518) (within `loadTab`)
* **The Code:**
  ```javascript
  async function loadTab(tab) {
    switch(tab) {
      case 'dashboard': if (!state.dashboard.loaded) await loadDashboard(); break;
      case 'graph': if (!state.graph.loaded) await loadGraph(); break;
      case 'memories': if (!state.memories.loaded) await loadMemories(); break;
      ...
    }
  }
  ```
* **Root Cause:** A tab's data is only fetched the first time it is visited (when `.loaded` is `false`).
* **Consequence:** Switching between tabs (e.g. from Dashboard to Sessions or Memories) displays stale cached data. The user has to hit the browser refresh button to see updates.
* **Remediation:** Force data reload when a tab is clicked, or provide a visual indicator and auto-refresh trigger when switching tabs:
  ```javascript
  // Always load fresh data on switch, or reset the loaded flag
  async function loadTab(tab) {
    switch(tab) {
      case 'dashboard': await loadDashboard(); break;
      case 'graph': await loadGraph(); break;
      case 'memories': await loadMemories(); break;
      ...
    }
  }
  ```

### 4. Flat Project Name Skipping in Graph Construction
* **File & Lines:** [src/viewer/index.html:L1871](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L1871) and [L1893](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/viewer/index.html#L1893) (within `loadGraph`)
* **The Code:**
  ```javascript
  if (!path.includes('/') && !path.includes('\\')) return;
  ```
* **Root Cause:** The graph builder ignores any session/memory whose project path does not contain a slash or backslash.
* **Consequence:** If project names are stored as simple strings (e.g. `agentmemory-python` or `demo_project` rather than absolute directories), they are omitted from the graph, rendering the Graph tab entirely empty (0 nodes, 0 edges).
* **Remediation:** Remove the slash-enforcement condition so any non-empty project name is rendered:
  ```javascript
  // Remove: if (!path.includes('/') && !path.includes('\\')) return;
  ```

### 5. Empty Graph Nodes Card on Dashboard
* **File & Lines:** [src/app.py:L990-996](file:///d:/Downloads/Projects/Other%20Projects/agentmemory-python/src/app.py#L990-L996) (within `api_graph_stats`)
* **Root Cause:** The Dashboard tab's "Graph Nodes" card displays stats queried from the backend `/graph/stats` endpoint. The backend queries `kv.list(KV.graphNodes)` and `kv.list(KV.graphEdges)`. However, the Python backend never writes to these scopes.
* **Consequence:** The "Graph Nodes" card always displays `0` even when the Graph tab successfully visualizes active sessions and folders.
* **Remediation:** Calculate these stats dynamically in `api_graph_stats` based on the counts of active unique project paths in the sessions and memories lists, matching the client-side folder map logic.
