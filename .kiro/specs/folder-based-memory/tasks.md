# Implementation Plan: Folder-Based Memory

## Overview

Restructure agentmemory-python from session-based to folder-based memory. The primary unit
of storage shifts from `(session_id)` to `(folder_path, agent_id)`. Core business logic
(`functions.py`) is rewritten first; REST endpoints and MCP tools (`app.py`) are updated
next; finally the viewer (`index.html`) is rebuilt to four tabs: Folders, Memories, Graph,
Timeline. A migration endpoint and a pytest + hypothesis test suite round out the work.

---

## Tasks

- [x] 1. Rewrite KV scope registry and core helpers in `src/functions.py`
  - [x] 1.1 Replace the `KV` class with the folder-based scope definitions
    - Remove all session/lesson/slot/action/crystal/sketch/facet/sentinel/signal/checkpoint/mesh/routine scopes
    - Add `KV.folders = "mem:folders"`, `KV.folder_obs(folder_path, agent_id)`, `KV.folder_meta(folder_path, agent_id)`
    - Keep `KV.memories`, `KV.bm25Index`, `KV.audit`; keep `KV.relations` (repurposed for graph edges)
    - _Requirements: REQ-001, REQ-004, REQ-005_

  - [x] 1.2 Add `normalize_folder_path()` utility function
    - Apply `os.path.normpath`, convert OS separators to `/`, strip leading/trailing slashes
    - Reject (raise `ValueError`) if the normalized path contains `..` segments
    - Cap `folder_path` and `agent_id` at 512 characters before any scope construction
    - _Requirements: REQ-002, REQ-063, REQ-064, REQ-066_

  - [ ]* 1.3 Write property test for `normalize_folder_path` — idempotency
    - **Property 8: Path Normalization Idempotency**
    - **Validates: REQ-074 / Requirements REQ-002**
    - Use `hypothesis.strategies.text()` with a mix of Windows and POSIX path strings
    - Assert `normalize_folder_path(normalize_folder_path(p)) == normalize_folder_path(p)` for all valid inputs

- [x] 2. Implement `folder_observe()` — observation ingestion
  - [x] 2.1 Write `folder_observe(kv, payload) -> dict` in `src/functions.py`
    - Validate required fields: `folderPath`, `agentId`, `text`, `timestamp`; raise `ValueError` if missing
    - Normalize `folder_path` via `normalize_folder_path()`; strip and validate `agent_id`
    - Call `strip_private_data(text)` before any storage; cap text at 4000 chars
    - Generate `obs_id = generate_id("fobs")`; build `FolderObservation` dict with all fields
    - Write to `KV.folder_obs(folder_path, agent_id)` under `obs_id`
    - Upsert `KV.folder_meta` (`obsCount += 1`, update `lastUpdated`); upsert `KV.folders` index entry
    - Add to `_bm25_index`; call `vector_index_add_guarded` if provider set; schedule persistence save
    - Write audit log entry via `kv.commit_version`; broadcast via `broadcast_stream`
    - Enforce `MAX_OBS_PER_FOLDER` cap (default 2000, env-configurable); raise `ValueError` if reached
    - _Requirements: REQ-003, REQ-007, REQ-008, REQ-009, REQ-010, REQ-011, REQ-012, REQ-013, REQ-014, REQ-015_

  - [ ]* 2.2 Write property test for `folder_observe` — pair isolation
    - **Property 1: Pair Isolation**
    - **Validates: REQ-068**
    - Generate two distinct `(folderPath, agentId)` pairs via `hypothesis`; write one observation to each
    - Assert `kv.list(KV.folder_obs(a)) ∩ kv.list(KV.folder_obs(b)) = ∅`

  - [ ]* 2.3 Write property test for `folder_observe` — observation count consistency
    - **Property 2: Observation Count Consistency**
    - **Validates: REQ-069**
    - Generate N random observations for a pair (N drawn from `hypothesis.strategies.integers(1, 20)`)
    - Assert `kv.get(KV.folder_meta(fp, aid), "meta")["obsCount"] == len(kv.list(KV.folder_obs(fp, aid)))`

  - [ ]* 2.4 Write property test for `folder_observe` — index coverage
    - **Property 3: Index Coverage**
    - **Validates: REQ-070**
    - After writing at least one observation for a pair, assert `kv.get(KV.folders, f"{fp}:{aid}")` is not None

  - [ ]* 2.5 Write property test for `folder_observe` — privacy invariant
    - **Property 4: Privacy Invariant**
    - **Validates: REQ-009 / REQ-065**
    - Inject strings matching `SECRET_PATTERN_SOURCES` patterns into `text`; call `folder_observe`
    - Assert no stored observation's `text` field matches any secret pattern

- [x] 3. Implement `folder_search()`, `folder_timeline()`, and `folder_graph_build()`
  - [x] 3.1 Write `folder_search(kv, query, limit, folder_path?, agent_id?) -> list` in `src/functions.py`
    - Delegate to existing `_hybrid_search.search(query, limit * 2)` for BM25 + vector scoring
    - Hydrate each `obs_id` from `KV.folder_obs` scopes; include matching global memories from `KV.memories`
    - Apply `folder_path` / `agent_id` post-filters; include `folderPath`, `agentId`, `score` in each result
    - _Requirements: REQ-016, REQ-017, REQ-018, REQ-019_

  - [x] 3.2 Write `folder_timeline(kv, limit, folder_path?, agent_id?, before?, after?) -> list`
    - List all entries from `KV.folders`; apply folder/agent filters; load observations from each pair
    - Apply `before` / `after` ISO timestamp filters; sort all results by `timestamp` descending; return `[:limit]`
    - _Requirements: REQ-020, REQ-021, REQ-022_

  - [ ]* 3.3 Write property test for `folder_timeline` — ordering guarantee
    - **Property 5: Timeline Ordering**
    - **Validates: REQ-071**
    - Generate an arbitrary set of observations with random timestamps; call `folder_timeline(kv, limit=1000)`
    - Assert `all(result[i]["timestamp"] >= result[i+1]["timestamp"] for i in range(len(result)-1))`

  - [x] 3.4 Write `folder_graph_build(kv) -> dict`
    - Build one node per unique `folder_path` in `KV.folders`; aggregate `agentIds` and `obsCount`; assign color via `folderColor(folder_path)` hash
    - Produce three edge types: `"same-parent"` (shared parent dir), `"cross-ref"` (folder B's path mentioned in folder A's obs text/title), `"agent-shared"` (two folders share an `agentId`)
    - Deduplicate edges on `(source, target, type)`; return `{"nodes": [...], "edges": [...]}`
    - _Requirements: REQ-023, REQ-024, REQ-025, REQ-026, REQ-027, REQ-028_

  - [ ]* 3.5 Write property test for `folder_graph_build` — node uniqueness
    - **Property 10: Graph Node Uniqueness**
    - **Validates: REQ-073 / Requirements REQ-023**
    - Generate observations in multiple `(folder, agent)` combinations sharing some folder paths
    - Assert `len(nodes) == len({entry["folderPath"] for entry in kv.list(KV.folders)})`

- [x] 4. Update `forget()` and `health_check()` / `export_data()` in `src/functions.py`
  - [x] 4.1 Rewrite `forget(kv, data) -> dict` to handle folder-based deletion
    - If `memoryId` present: delete from `KV.memories` (existing logic, unchanged)
    - If `folderPath + agentId` present (no `observationIds`): delete all entries in `KV.folder_obs(fp, aid)`, remove BM25 index entries for each `obs_id`, delete `KV.folder_meta` entry, remove `KV.folders` index entry; set `result["deleted"]`
    - If `folderPath + agentId + observationIds` present: delete only specified observations, decrement `obsCount` in metadata
    - _Requirements: REQ-029, REQ-030, REQ-031, REQ-032, REQ-033_

  - [ ]* 4.2 Write property test for `forget` — completeness
    - **Property 6: Forget Completeness**
    - **Validates: REQ-072**
    - Write N observations for a pair; call `forget({folderPath, agentId})`
    - Assert `kv.list(KV.folder_obs(fp, aid)) == []`, `kv.get(KV.folders, key) is None`, and no BM25 entry matches any `obs_id` from that pair

  - [x] 4.3 Update `health_check(kv) -> dict` to return folder-based counts
    - Return `folderCount` (distinct folder paths), `agentCount`, `pairCount`, `observationCount`, `memoryCount`, `bm25IndexSize`, `vectorIndexSize`, `dbPath`
    - Remove session-based fields
    - _Requirements: REQ-047_

  - [x] 4.4 Update `export_data(kv, opts) -> dict` to v2 folder format
    - Output `{"folders": [{folderPath, agentId, meta, observations: [...]}], "memories": [...], "exportedAt": ..., "version": "2.0"}`
    - _Requirements: REQ-045_

  - [x] 4.5 Update `rebuild_index(kv)` to read from folder observation scopes
    - Iterate `KV.folders` index entries; load each pair's observations via `KV.folder_obs`; re-add to BM25 and vector indexes
    - _Requirements: REQ-016_

- [x] 5. Checkpoint — verify core business logic
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Update REST endpoints in `src/app.py`
  - [x] 6.1 Rewrite `POST /agentmemory/agent/observe` to call `folder_observe()`
    - Require `folderPath`, `agentId`, `text`; accept `sessionId` for backward compat (ignore it)
    - Validate with HTTP 400 if required fields are missing; pass through `folderPath`, `agentId`, `text`, `timestamp`, `type`, `title`, `concepts`, `files`, `importance`
    - _Requirements: REQ-008, REQ-041, REQ-057_

  - [x] 6.2 Update `POST /agentmemory/search` to call `folder_search()`
    - Accept optional `folderPath` and `agentId` in request body; pass to `folder_search()`
    - _Requirements: REQ-016, REQ-017, REQ-057_

  - [ ] 6.3 Add `GET /agentmemory/folders` endpoint
    - Return `kv.list(KV.folders)` sorted by `lastUpdated` descending
    - _Requirements: REQ-052, REQ-057_

  - [x] 6.4 Add `GET /agentmemory/folder/observations` endpoint
    - Accept `folderPath` and `agentId` query params (both required); return `{"observations": [...]}`
    - Reject with HTTP 400 if either param is missing
    - _Requirements: REQ-053, REQ-057_

  - [x] 6.5 Add `POST /agentmemory/timeline` endpoint
    - Accept optional JSON body `{folderPath?, agentId?, limit?, before?, after?}`; delegate to `folder_timeline()`
    - _Requirements: REQ-054, REQ-057_

  - [x] 6.6 Add `GET /agentmemory/graph` endpoint
    - Delegate to `folder_graph_build(kv)`; return `{"nodes": [...], "edges": [...]}`
    - _Requirements: REQ-055, REQ-057_

  - [x] 6.7 Add `POST /agentmemory/migrate` endpoint
    - Accept optional `dry_run` boolean in body; delegate to `migrate_sessions_to_folders(kv, dry_run)`
    - Return `{migrated_sessions, migrated_observations, errors}`
    - _Requirements: REQ-056, REQ-057, REQ-058, REQ-059, REQ-060, REQ-061, REQ-062_

  - [x] 6.8 Implement `migrate_sessions_to_folders(kv, dry_run=False)` in `src/functions.py`
    - Read `kv.list("mem:sessions")`; for each session, map `cwd` or `project` → `folderPath`, `agentId` with `"unknown"` fallback
    - Skip `:raw` observations; build `FolderObservation` from session obs fields; write if not dry-run
    - Upsert folder metadata and index entries; never delete old `mem:sessions` or `mem:obs:*` scopes
    - Collect per-session errors; return `{migrated_sessions, migrated_observations, errors}`
    - _Requirements: REQ-058, REQ-059, REQ-060, REQ-061, REQ-062_

  - [x] 6.9 Remove or stub out session-based endpoints that have no folder equivalent
    - Remove/stub: `POST /session/start`, `POST /session/end`, `POST /session/commit`, `GET /session/by-commit`, `GET /sessions`, `GET /observations` (session-scoped), `GET /replay/sessions`
    - Remove/stub: lessons endpoints (`/lessons`, `/lessons/search`, `/lessons/strengthen`), slots endpoints (`/slot`, `/slots`, `/slot/append`, `/slot/replace`), antigravity endpoints
    - Remove background worker calls to `lesson_decay_sweep` and `consolidate` from `init_app()`
    - _Requirements: REQ-040, REQ-051_

- [x] 7. Update MCP tool schema and dispatch in `src/app.py`
  - [x] 7.1 Update `agent_observe` MCP tool schema and handler
    - Change required fields to `folderPath`, `agentId`, `text`; mark `sessionId` as optional/deprecated
    - Handler calls `folder_observe(kv, args)`
    - _Requirements: REQ-041_

  - [x] 7.2 Update `memory_recall` and `memory_smart_search` MCP tool schemas and handlers
    - Add optional `folderPath` and `agentId` parameters; handlers call `folder_search()`
    - _Requirements: REQ-042, REQ-044_

  - [x] 7.3 Update `memory_export`, `memory_forget`, `memory_diagnose` MCP handlers
    - `memory_export` calls updated `export_data()` → v2 format
    - `memory_forget` accepts `memoryId` OR `folderPath + agentId`; calls updated `forget()`
    - `memory_diagnose` calls updated `health_check()` → folder counts
    - _Requirements: REQ-045, REQ-046, REQ-047_

  - [x] 7.4 Add `memory_folders`, `memory_folder_observations`, `memory_timeline` MCP tools
    - `memory_folders`: no required args; returns `kv.list(KV.folders)`
    - `memory_folder_observations`: requires `folderPath`, `agentId`; returns observations for that pair
    - `memory_timeline`: optional `folderPath`, `agentId`, `limit`, `before`, `after`; calls `folder_timeline()`
    - _Requirements: REQ-048, REQ-049, REQ-050_

  - [x] 7.5 Remove dropped MCP tools from schema and dispatch
    - Remove from `GET /mcp/tools` list and `POST /mcp/tools` dispatch: `memory_sessions`, `memory_sessions_list`, `memory_observations` (session), `memory_profile`, `memory_lessons`, `memory_lesson_save`, `memory_lesson_recall`, `memory_lesson_search`, `memory_consolidate`, `memory_reflect`, `memory_crystallize`, `memory_slot_list`, `memory_slot_get`, `memory_slot_create`, `memory_slot_append`, `memory_slot_replace`, `memory_slot_delete`, `memory_action_create`, `memory_action_update`, `memory_frontier`, `memory_antigravity_sync`, `memory_antigravity_sync_all`
    - _Requirements: REQ-051_

- [x] 8. Checkpoint — verify REST and MCP layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Rebuild viewer dashboard to four tabs in `src/viewer/index.html`
  - [x] 9.1 Remove old tabs and restructure tab bar to exactly four tabs
    - Replace current tab bar entries with: Folders, Memories, Graph, Timeline
    - Remove tab panes and JS sections for: Sessions, Lessons, Slots, Actions, Replay, Profile, Crystals
    - Update `<div class="tab-bar">` and the `showTab()` / active-tab JS logic accordingly
    - _Requirements: REQ-034, REQ-040_

  - [x] 9.2 Implement Folders tab — list view and drill-down detail
    - On load, fetch `GET /agentmemory/folders`; render table rows with `folderPath`, `agentId`, `obsCount`, `lastUpdated`
    - On row click, fetch `GET /agentmemory/folder/observations?folderPath=&agentId=`; render observations list with `timestamp`, type badge, `title`, `text` excerpt
    - Show `summary` field if present; add "Delete folder memory" button that calls `POST /agentmemory/forget`
    - _Requirements: REQ-035, REQ-036_

  - [x] 9.3 Preserve Memories tab functionality (no logic change)
    - Verify existing Memories tab code still works against `GET /agentmemory/memories` and `POST /agentmemory/remember`
    - Update any session-specific references (e.g. session filter dropdowns) to remove or replace with folder filters
    - _Requirements: REQ-037_

  - [x] 9.4 Implement Graph tab with folder-based nodes and edges
    - Fetch `GET /agentmemory/graph`; render force-directed graph using existing canvas + physics code
    - Nodes keyed by `folderPath`; color via existing `folderColor()` function; node label = basename of `folderPath`
    - Render three edge types with distinct stroke styles: `same-parent` (dashed), `cross-ref` (solid), `agent-shared` (dotted)
    - Update graph sidebar filter checkboxes to match the three new edge types
    - _Requirements: REQ-038_

  - [x] 9.5 Implement Timeline tab with folder activity feed
    - Fetch `POST /agentmemory/timeline` with `{limit: 100}` on tab activate; render observation cards sorted newest-first
    - Add filter bar: text input for `folderPath` substring match, dropdown for `agentId` (populated from `GET /agentmemory/folders`)
    - On filter change, re-fetch timeline with updated body params; support `before`/`after` via existing date-range controls pattern
    - _Requirements: REQ-039_

  - [x] 9.6 Update WebSocket handler to process `folder_observation` broadcast events
    - Change the `type == "compressed_observation"` / `type == "raw_observation"` branch to also handle `type == "folder_observation"`
    - On receive, append to the Timeline tab live feed if it is active; update Folders tab obsCount badge if that folder is visible
    - _Requirements: REQ-013_

- [x] 10. Update supporting files
  - [x] 10.1 Update `requirements.txt` to add `pytest` and `hypothesis`
    - Add `pytest==8.3.5` and `hypothesis==6.131.15` under a `# dev` comment
    - _Requirements: design testing strategy_

  - [x] 10.2 Update `start.sh` to remove session-based startup logic
    - Remove any session auto-complete or lesson-decay invocations from startup
    - _Requirements: REQ-040, REQ-051_

  - [x] 10.3 Update `sync.py` if it references session-scoped KV keys
    - Replace any hardcoded `mem:sessions` or `mem:obs:*` references with `mem:folders` and `mem:folder:*` patterns for backup filtering
    - _Requirements: REQ-004_

- [x] 11. Write pytest + hypothesis test suite
  - [x] 11.1 Create `tests/test_normalize.py` — unit tests for `normalize_folder_path`
    - Test Windows paths (`C:\Users\foo`), POSIX paths, paths with `..`, UNC paths, trailing slashes, empty string (expect `ValueError`)
    - _Requirements: REQ-002, REQ-063_

  - [x] 11.2 Create `tests/test_folder_observe.py` — unit tests for `folder_observe`
    - Test missing required fields raise `ValueError`; test obs ID prefix `fobs_`; test `obsCount` increment; test `KV.folders` index upsert; test `MAX_OBS_PER_FOLDER` cap
    - _Requirements: REQ-008, REQ-010, REQ-011, REQ-015_

  - [x] 11.3 Create `tests/test_forget.py` — unit tests for `forget`
    - Test full pair deletion clears all three scopes; test partial deletion by `observationIds`; test `deleted` count in response
    - _Requirements: REQ-029, REQ-030, REQ-031, REQ-032, REQ-033_

  - [x] 11.4 Create `tests/test_timeline.py` — unit tests for `folder_timeline`
    - Test `before`/`after` filters; test `limit` enforcement; test empty result when no data
    - _Requirements: REQ-020, REQ-021, REQ-022_

  - [x] 11.5 Create `tests/test_graph.py` — unit tests for `folder_graph_build`
    - Test empty KV returns `{nodes: [], edges: []}`; test one node per unique folder; test same-parent edge; test no duplicate edges
    - _Requirements: REQ-023, REQ-025, REQ-028_

  - [x] 11.6 Create `tests/test_migration.py` — unit tests for `migrate_sessions_to_folders`
    - Test `dry_run=True` writes nothing; test `cwd`/`project` fallback to `"unknown"`; test `:raw` obs are skipped; test `errors` list populated on malformed sessions
    - _Requirements: REQ-058, REQ-059, REQ-060, REQ-061, REQ-062_

  - [ ]* 11.7 Create `tests/test_properties.py` — all Hypothesis property tests
    - **Property 1: Pair Isolation** (REQ-068)
    - **Property 2: Observation Count Consistency** (REQ-069)
    - **Property 3: Index Coverage** (REQ-070)
    - **Property 4: Privacy Invariant** (REQ-065)
    - **Property 5: Timeline Ordering** (REQ-071)
    - **Property 6: Forget Completeness** (REQ-072)
    - **Property 7: Memory Version Uniqueness** (REQ-073)
    - **Property 8: Path Normalization Idempotency** (REQ-074)
    - **Property 10: Graph Node Uniqueness** (REQ-023)
    - Use a temp-file SQLite DB (`tmp_path` fixture) for each test; isolate with `@given` + `@settings(max_examples=50)`

- [x] 12. Final checkpoint — full test run
  - Ensure all tests pass (`pytest tests/`), ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- The migration endpoint (task 6.8) is non-destructive: old `mem:sessions` / `mem:obs:*` data is never deleted
- Property tests in `tests/test_properties.py` (task 11.7) consolidate all Hypothesis tests; the earlier inline property test tasks (1.3, 2.2–2.5, 3.3, 3.5, 4.2) are equivalent sub-sets and can be skipped if 11.7 is implemented
- `src/db.py` schema is unchanged — only KV scope strings change, not the SQLite table structure
- The `folderColor()` function referenced by the Graph tab and `folder_graph_build()` is the existing hash-to-HSL function already in `index.html`; it should be extracted or duplicated into `functions.py` for server-side use

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "3.1", "3.2", "4.1"] },
    { "id": 3, "tasks": ["3.3", "3.4", "4.2", "4.3", "4.4", "4.5"] },
    { "id": 4, "tasks": ["3.5", "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.8"] },
    { "id": 5, "tasks": ["6.7", "6.9", "7.1", "7.2", "7.3"] },
    { "id": 6, "tasks": ["7.4", "7.5", "9.1", "10.1", "10.2", "10.3"] },
    { "id": 7, "tasks": ["9.2", "9.3", "9.4", "9.5"] },
    { "id": 8, "tasks": ["9.6", "11.1", "11.2", "11.3", "11.4", "11.5", "11.6"] },
    { "id": 9, "tasks": ["11.7"] }
  ]
}
```
