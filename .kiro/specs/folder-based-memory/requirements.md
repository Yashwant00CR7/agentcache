# Requirements: Folder-Based Memory

## Overview

This document specifies the requirements for restructuring agentmemory-python from a session-based architecture to a folder-based memory model. The primary unit of storage shifts from `(session_id)` to `(folder_path, agent_id)` — each agent accumulates observations scoped to the folder it is working in, with no concept of "sessions". Long-term global memories remain unchanged. The viewer, MCP tools, and REST API are all reshaped around this model.

The redesign eliminates sessions, lessons, slots, actions, crystals, and artefacts entirely. What remains is: folder observations, global memories, BM25 + optional vector search, a folder-based graph, a folder activity feed (timeline), privacy stripping, and the MCP tool-calling interface.

---

## Group 1 — Data Model

- **REQ-001:** The system SHALL store observations scoped to `(folder_path, agent_id)` pairs, replacing the existing session-based `(session_id)` scope.
- **REQ-002:** The system SHALL normalize all folder paths using `os.path.normpath`, converting OS separators to forward slashes and stripping leading/trailing slashes before any storage or scope construction.
- **REQ-003:** Each observation SHALL contain the following fields: `id`, `folderPath`, `agentId`, `timestamp`, `text`, `type`, `title`, `concepts`, `files`, and `importance`.
- **REQ-004:** The system SHALL maintain a global folders index stored in the `mem:folders` KV scope, keyed by the string `"{folder_path}:{agent_id}"`, containing `folderPath`, `agentId`, `lastUpdated`, and `obsCount`.
- **REQ-005:** The system SHALL maintain per-pair metadata in a `mem:foldermeta:{folder_path}:{agent_id}` KV scope containing `folderPath`, `agentId`, `lastUpdated`, `obsCount`, and an optional `summary` field.
- **REQ-006:** Global memories stored in the `mem:memories` scope SHALL remain unchanged from the current implementation, including their data model, versioning behaviour, and Jaccard deduplication.
- **REQ-007:** The system SHALL cap observation `text` at 4000 characters before storage.

---

## Group 2 — Observation Ingestion (`agent_observe`)

- **REQ-008:** The `agent_observe` endpoint SHALL require the fields `folderPath`, `agentId`, `text`, and `timestamp`; requests missing any of these fields SHALL be rejected with an error response.
- **REQ-009:** The system SHALL call `strip_private_data()` on the observation text before any storage or indexing operation.
- **REQ-010:** The system SHALL generate a unique observation ID with the prefix `fobs_` for every new observation.
- **REQ-011:** The system SHALL upsert the `mem:folders` index entry for the `(folder_path, agent_id)` pair on every successful observation write.
- **REQ-012:** The system SHALL add each new observation to the BM25 index, and to the vector index if an embedding provider is configured.
- **REQ-013:** The system SHALL broadcast each new observation via the existing WebSocket mechanism to connected viewers.
- **REQ-014:** The system SHALL write an audit log entry in `mem:audit` for each observation ingestion.
- **REQ-015:** The system SHALL enforce a per-pair observation cap (`MAX_OBS_PER_FOLDER`, default 2000, configurable via environment variable); ingestion SHALL be rejected when the cap is reached.

---

## Group 3 — Search

- **REQ-016:** The system SHALL support hybrid BM25 + vector search across all folder observations, combining scores via Reciprocal Rank Fusion (RRF) using the existing `HybridSearch` implementation.
- **REQ-017:** Search SHALL accept optional `folderPath` and `agentId` filter parameters and restrict results to matching pairs when provided.
- **REQ-018:** Search results SHALL also include matching global memories from `mem:memories` alongside folder observations.
- **REQ-019:** Each search result SHALL include `folderPath`, `agentId`, `score`, and the full observation object.

---

## Group 4 — Timeline

- **REQ-020:** The system SHALL provide a folder activity feed that returns observations sorted by `timestamp` descending.
- **REQ-021:** The timeline SHALL support filtering by `folderPath`, `agentId`, `before` (ISO 8601 timestamp upper bound), and `after` (ISO 8601 timestamp lower bound).
- **REQ-022:** The timeline SHALL respect a `limit` parameter (default 100) and return at most that many observations.

---

## Group 5 — Graph

- **REQ-023:** The system SHALL build a graph where each unique `folder_path` (regardless of how many agents have observations in it) is represented as exactly one node.
- **REQ-024:** Each graph node SHALL contain the fields `id`, `label`, `folderPath`, `agentIds` (list of all agents with observations in that folder), `obsCount`, and `color` (derived via the existing `folderColor()` hash function).
- **REQ-025:** The system SHALL create "same-parent" edges between any two folder nodes that share the same parent directory.
- **REQ-026:** The system SHALL create "cross-reference" edges between folder A and folder B when the text or title of any observation in folder A contains folder B's path string.
- **REQ-027:** The system SHALL create "agent-shared" edges between any two folder nodes that have at least one common `agentId` with observations.
- **REQ-028:** The graph SHALL contain no duplicate edges; an edge is a duplicate if it shares the same `source`, `target`, and `type` as an existing edge.

---

## Group 6 — Forget / Delete

- **REQ-029:** The `forget` function SHALL accept a `memoryId` parameter to delete a single global memory from `mem:memories`.
- **REQ-030:** The `forget` function SHALL accept a `folderPath` + `agentId` pair to delete all observations stored for that pair.
- **REQ-031:** The `forget` function SHALL accept a `folderPath` + `agentId` pair together with a list of `observationIds` to delete only the specified observations.
- **REQ-032:** When a full `(folderPath, agentId)` pair is deleted, the system SHALL remove its entry from the `mem:folders` index, delete the `mem:foldermeta:{path}:{agent}` scope, and remove all corresponding entries from the BM25 index.
- **REQ-033:** The response from any `forget` operation SHALL include a `"deleted"` field containing the count of items actually removed from storage.

---

## Group 7 — Viewer (4 Tabs Only)

- **REQ-034:** The viewer dashboard SHALL contain exactly four tabs: Folders, Memories, Graph, and Timeline.
- **REQ-035:** The Folders tab SHALL list all `(folder, agent)` pairs, displaying `folderPath`, `agentId`, `obsCount`, and `lastUpdated` for each.
- **REQ-036:** Clicking a row in the Folders tab SHALL display a drill-down view showing all observations for that `(folderPath, agentId)` pair.
- **REQ-037:** The Memories tab SHALL be functionally unchanged from the current implementation.
- **REQ-038:** The Graph tab SHALL render a force-directed graph using the folder-based node and edge model defined in Group 5.
- **REQ-039:** The Timeline tab SHALL display observations from all pairs sorted by timestamp descending, with filter controls for folder path and agent ID.
- **REQ-040:** The following tabs SHALL be removed from the viewer: Sessions, Lessons, Slots, Actions, Replay, Profile, and Crystals.

---

## Group 8 — MCP Tools

- **REQ-041:** The `agent_observe` MCP tool SHALL require `folderPath`, `agentId`, and `text`; a `sessionId` parameter MAY be accepted for backward compatibility but SHALL be ignored.
- **REQ-042:** The `memory_recall` MCP tool SHALL search folder observations and global memories, accepting optional `folderPath` and `agentId` filter parameters.
- **REQ-043:** The `memory_save` and `agent_remember` MCP tools SHALL continue to save to global memories without change.
- **REQ-044:** The `memory_smart_search` MCP tool SHALL perform hybrid BM25 + vector search equivalent to `memory_recall`.
- **REQ-045:** The `memory_export` MCP tool SHALL export all folder pairs with their observations and all global memories in the v2 export format (version `"2.0"`).
- **REQ-046:** The `memory_forget` MCP tool SHALL accept either a `memoryId` (global memory deletion) or a `folderPath` + `agentId` pair (folder observations deletion).
- **REQ-047:** The `memory_diagnose` MCP tool SHALL return `folderCount`, `agentCount`, `pairCount`, `observationCount`, and `memoryCount` instead of session-based counts.
- **REQ-048:** A new `memory_folders` MCP tool SHALL be added that lists all `(folder, agent)` pairs with `folderPath`, `agentId`, `obsCount`, and `lastUpdated`.
- **REQ-049:** A new `memory_folder_observations` MCP tool SHALL be added that returns all observations for a specified `(folderPath, agentId)` pair.
- **REQ-050:** A new `memory_timeline` MCP tool SHALL be added that returns the folder activity feed, supporting the same filter parameters as the timeline REST endpoint.
- **REQ-051:** The following MCP tools SHALL be removed: `memory_sessions`, `memory_sessions_list`, `memory_observations` (session-based), `memory_profile`, `memory_lessons`, `memory_lesson_save`, `memory_lesson_recall`, `memory_lesson_search`, `memory_consolidate`, `memory_reflect`, `memory_crystallize`, `memory_slot_list`, `memory_slot_get`, `memory_slot_create`, `memory_slot_append`, `memory_slot_replace`, `memory_slot_delete`, `memory_action_create`, `memory_action_update`, `memory_frontier`, `memory_antigravity_sync`, and `memory_antigravity_sync_all`.

---

## Group 9 — REST Endpoints

- **REQ-052:** The system SHALL expose `GET /agentmemory/folders` to return a list of all `(folder, agent)` pairs from the `mem:folders` index.
- **REQ-053:** The system SHALL expose `GET /agentmemory/folder/observations` accepting `folderPath` and `agentId` query parameters and returning all observations for that pair.
- **REQ-054:** The system SHALL expose `POST /agentmemory/timeline` accepting an optional JSON body with `folderPath`, `agentId`, `limit`, `before`, and `after` fields, and returning the folder activity feed.
- **REQ-055:** The system SHALL expose `GET /agentmemory/graph` returning the graph payload with `nodes` and `edges` arrays.
- **REQ-056:** The system SHALL expose `POST /agentmemory/migrate` to migrate old session-based data to the folder format, supporting a `dry_run` boolean parameter.
- **REQ-057:** All new endpoints SHALL enforce `AGENTMEMORY_SECRET` Bearer token authentication using `hmac.compare_digest`, consistent with the existing `check_auth()` pattern; the `/livez` endpoint SHALL remain unauthenticated.

---

## Group 10 — Migration

- **REQ-058:** The `POST /agentmemory/migrate` endpoint SHALL read from the existing `mem:sessions` and `mem:obs:{session_id}` KV scopes to retrieve legacy session data.
- **REQ-059:** Migration SHALL map `session.cwd` or `session.project` to `folderPath`, and `session.agentId` to `agentId`; missing values SHALL fall back to the string `"unknown"`.
- **REQ-060:** Migration SHALL be non-destructive: old `mem:sessions` and `mem:obs:*` scopes SHALL never be automatically deleted during or after migration.
- **REQ-061:** Migration SHALL support a `dry_run=true` mode that computes and returns migration counts without writing any data.
- **REQ-062:** The migration response SHALL include the fields `migrated_sessions`, `migrated_observations`, and `errors`.

---

## Group 11 — Security & Privacy

- **REQ-063:** The system SHALL apply `os.path.normpath` to all folder paths before using them in any KV scope key construction.
- **REQ-064:** The system SHALL reject with HTTP 400 any request where the normalized folder path contains path traversal patterns (e.g. `..`).
- **REQ-065:** The system SHALL call `strip_private_data()` on all observation text before storage, indexing, or broadcast.
- **REQ-066:** The system SHALL length-cap `agent_id` and `folder_path` values at 512 characters before using them in any KV scope key construction.
- **REQ-067:** All new REST endpoints SHALL use the existing `check_auth()` / `hmac.compare_digest` authentication pattern.

---

## Group 12 — Correctness Properties

- **REQ-068:** The system SHALL guarantee pair isolation: a direct read of `KV.folder_obs(folder_path_A, agent_id_A)` SHALL never return observations belonging to a different `(folder_path, agent_id)` pair.
- **REQ-069:** The system SHALL guarantee observation count consistency: the `obsCount` value stored in `KV.folder_meta(folder_path, agent_id)` SHALL always equal the actual number of observation keys present in `KV.folder_obs(folder_path, agent_id)`.
- **REQ-070:** The system SHALL guarantee index coverage: every `(folder_path, agent_id)` pair that has at least one stored observation SHALL have a corresponding entry in the `mem:folders` index.
- **REQ-071:** The system SHALL guarantee timeline ordering: all results returned by `folder_timeline()` SHALL be in non-increasing `timestamp` order (i.e. `result[i].timestamp >= result[i+1].timestamp` for all valid `i`).
- **REQ-072:** The system SHALL guarantee forget completeness: after a full `(folderPath, agentId)` pair deletion, the BM25 index SHALL contain no document entries originating from that pair.
- **REQ-073:** The system SHALL guarantee memory version uniqueness: at most one memory with a Jaccard similarity greater than 0.7 to any other memory SHALL have `isLatest=True` at any point in time.
- **REQ-074:** The system SHALL guarantee path normalization idempotency: applying `normalize_folder_path()` twice to any input SHALL produce the same result as applying it once — i.e. `normalize_folder_path(normalize_folder_path(p)) == normalize_folder_path(p)`.
