# Requirements: Deduplication & Dashboard Fixes

## Overview
Agents are producing duplicate observations in the folder memory store, causing memory bloat and potential hallucination. The viewer dashboard has broken navigation/delete buttons and lacks per-observation delete. This spec addresses the root cause and the UI gaps.

---

## Requirements

### REQ-01: Observation Deduplication in `folder_observe`
- The system MUST compute a SHA-256 content fingerprint over `(folder_path, agent_id, text_normalized)` for every incoming observation.
- If an observation with the same fingerprint already exists in `KV.folder_obs(folder_path, agent_id)`, the system MUST return the existing observation ID without writing a duplicate.
- The fingerprint key stored in KV MUST use the scope `mem:obs_dedup:{folder_path}:{agent_id}` with key = fingerprint hex, value = existing obs_id.
- The dedup check MUST happen after normalization and private-data stripping but before writing to KV.
- Duplicate detection MUST be atomic: check + write must be protected against concurrent writes by a per-(folder, agent) lock.
- The response for a deduplicated observation MUST include `{"observationId": "<existing_id>", "deduplicated": true}`.

### REQ-02: One-Shot Deduplication Cleanup Endpoint
- A new REST endpoint `POST /agentmemory/folder/dedup` MUST be added.
- It MUST accept `{"folderPath": "...", "agentId": "..."}` in the body.
- It MUST scan all observations for that pair, group by normalized text fingerprint, keep the earliest observation per group, delete all later duplicates using the existing `forget` path, and rebuild the dedup index.
- If `folderPath` and `agentId` are both omitted, it MUST run across ALL folder pairs.
- It MUST return `{"deduplicated": <count_removed>, "pairs_processed": <n>, "kept": <count_kept>}`.
- Auth check MUST be applied.

### REQ-03: Per-Observation Delete in Folder Detail View
- The folder detail view in the viewer MUST show a "Delete" button on each observation card.
- Clicking delete on an observation MUST call `POST /agentmemory/forget` with `{"folderPath": "...", "agentId": "...", "observationIds": ["<id>"]}`.
- After deletion the observation card MUST be removed from the DOM without a full page reload.
- A confirmation modal MUST be shown before deletion (reuse the existing modal pattern).

### REQ-04: Bulk Delete with Checkbox Selection
- Each observation card in the folder detail view MUST have a checkbox.
- A "Select all" checkbox MUST appear in the toolbar above the list.
- A "Delete selected (N)" button MUST appear in the toolbar, enabled only when ≥1 checkbox is checked.
- Clicking "Delete selected" MUST call `POST /agentmemory/forget` with all selected observation IDs in a single request.
- After bulk deletion all deleted cards MUST be removed from the DOM.

### REQ-05: Fix Back Button in Folder Detail
- The "← Back" button MUST navigate back to the folder list without a full page reload.
- It MUST use the existing `data-action` event delegation pattern rather than inline `onclick`.

### REQ-06: Fix Delete Folder Button
- The "Delete folder memory" button MUST use `data-action` event delegation.
- It MUST NOT use inline `onclick` with string-escaped arguments (brittle with special characters in paths).
- The folder path and agent ID MUST be stored in `data-` attributes on the button element.
- After successful deletion it MUST navigate back to the folder list.

### REQ-07: Fix Tools Tab `apiFetchRaw` Double-Parse Bug
- The Tools tab "Run" button calls `apiFetchRaw()` then `.text()` on the result, but `apiFetchRaw` already calls `.json()` — this throws a TypeError.
- The fix MUST use the raw `fetch()` response directly in the Tools tab runner so `.text()` can be called on the `Response` object, not on a parsed JS object.

### REQ-08: Add `POST /agentmemory/folder/dedup` to MCP Tools
- The dedup endpoint MUST be exposed as an MCP tool named `memory_dedup`.
- Schema: `{"folderPath": string (optional), "agentId": string (optional)}`.
- It MUST appear in `GET /agentmemory/mcp/tools` and be dispatchable via `POST /agentmemory/mcp/tools`.
