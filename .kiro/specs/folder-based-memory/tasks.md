# Implementation Plan: agentmemory-python — Upscale & UX Overhaul

## Overview

The folder-based memory migration is complete. This plan covers the next phase:
structural refactoring to eliminate the god-file problem, UX uplift on the viewer
dashboard, reliability improvements to the storage and indexing layers, and new
developer-facing features. Work is organized into four parallel tracks that can be
assigned to different engineers independently.

**Track A — Backend Structure** (Senior Backend)
**Track B — Viewer UX** (Frontend / Full-Stack)
**Track C — Reliability & Testing** (Backend / QA)
**Track D — Integrations & DX** (DevEx / Infra)

---

## Tasks

## Track A — Backend Structure Refactor

> Owner: Senior Backend Dev
> Goal: Break the god files apart so the codebase scales to parallel contributions.
> Priority: CRITICAL — blocks all other backend work from being clean.

- [x] A1. Split `src/app.py` into Flask blueprints
  - [x] A1.1 Create `src/routes/` directory with `__init__.py`
    - Move observation routes (`/observe`, `/agent/observe`, `/folder/observations`, `/folders`) → `src/routes/observations.py`
    - Move memory routes (`/remember`, `/agent/remember`, `/memories`, `/forget`) → `src/routes/memories.py`
    - Move search + timeline routes (`/search`, `/timeline`) → `src/routes/search.py`
    - Move graph routes (`/graph`, `/graph/stats`, `/graph/query`) → `src/routes/graph.py`
    - Move health + audit routes (`/livez`, `/health`, `/audit`, `/config/flags`) → `src/routes/health.py`
    - Move MCP routes (`/mcp/tools` GET + POST) → `src/routes/mcp.py`
    - Move migration route (`/migrate`) → `src/routes/migration.py`
    - _Each blueprint file registers with `Blueprint(__name__)` and imports only what it needs_

  - [x] A1.2 Rebuild `src/app.py` as a thin factory
    - `create_app()` factory function: init DB, init embeddings, register all blueprints, set up WebSocket, register `after_request` CORS hook
    - Keep `init_app()` logic but move background thread setup into a separate `src/workers.py`
    - `if __name__ == "__main__"` block stays minimal — just `create_app().run()`
    - Target: `app.py` under 150 lines after refactor

  - [x] A1.3 Validate no route regressions after blueprint split
    - Run full `pytest tests/` suite; manually hit every endpoint via `/livez` sanity pass
    - Check WebSocket still connects and broadcasts after split

- [ ] A2. Split `src/functions.py` into focused modules
  - [ ] A2.1 Create `src/memory/` package
    - `src/memory/observe.py` — `folder_observe()`, `observe()`, `build_synthetic_compression()`, `strip_private_data()`
    - `src/memory/remember.py` — `remember()`, `forget()`, `jaccard_similarity()`
    - `src/memory/context.py` — `context()`, `export_data()`, `rebuild_index()`
    - `src/memory/graph.py` — `folder_graph_build()`, `get_relations()`, `add_relation()`, `folderColor()`
    - `src/memory/timeline.py` — `folder_timeline()`, `folder_search()`
    - `src/memory/health.py` — `health_check()`, `auto_forget()`

  - [~] A2.2 Keep `src/functions.py` as a compatibility shim
    - Re-export everything from `src/memory/*` via `from memory.observe import *` etc.
    - This avoids breaking `app.py` imports during the transition period
    - Remove the shim in a follow-up PR once blueprints are fully referencing the new modules

  - [~] A2.3 Move `KV` class and path utilities to `src/storage/`
    - `src/storage/scopes.py` — `KV` class with all scope definitions
    - `src/storage/paths.py` — `normalize_folder_path()`, `validate_agent_id()`, `generate_id()`, `fingerprint_id()`
    - `src/storage/images.py` — `save_image_to_disk()`, `delete_image()`, `touch_image()`, `is_managed_image_path()`

- [ ] A3. Fix `src/db.py` connection management
  - [~] A3.1 Implement per-thread persistent connections using `threading.local()`
    - Replace every `conn = self._get_conn()` / `conn.close()` pattern with a `_local = threading.local()` cache
    - Connection is opened once per thread and reused; close only on explicit `teardown()` or thread exit
    - Keep the write `_lock` for serializing INSERT/UPDATE/DELETE operations

  - [~] A3.2 Add WAL checkpoint on graceful shutdown
    - Register a `SIGTERM` / `atexit` handler in `src/workers.py` that calls `PRAGMA wal_checkpoint(FULL)` before exit
    - This ensures the BM25/vector index shards are flushed to the DB before the process dies

  - [~] A3.3 Add `db.stats()` method for the health endpoint
    - Return `{"db_size_bytes": ..., "kv_row_count": ..., "audit_row_count": ..., "wal_size_bytes": ...}`
    - Used by the upgraded `/health` endpoint

- [ ] A4. Debounce index persistence writes
  - [~] A4.1 Replace `IndexPersistence.schedule_save()` with a real debounce queue
    - Use a `threading.Timer` that resets on each call; fires the actual `save()` after 5 seconds of inactivity
    - This prevents a save on every single observation write under high throughput

  - [~] A4.2 Add dirty-flag tracking to `SearchIndex` and `VectorIndex`
    - Set `self._dirty = True` in `add()` and `remove()`; reset in `save()`
    - Skip `save()` entirely if `not _dirty`

  - [~] A4.3 Write unit test for debounce behavior
    - Assert that 100 rapid `add()` calls result in exactly 1 persistence save, not 100

---

## Track B — Viewer UX Overhaul

> Owner: Frontend / Full-Stack Dev
> Goal: Elevate the viewer from a debug tool to a real developer dashboard.
> Priority: HIGH — highest user-visible impact.

- [ ] B1. Add Command Palette (`Ctrl+K` / `Cmd+K`)
  - [~] B1.1 Build command palette overlay in `index.html`
    - Fuzzy-search across: folder names, memory content snippets, observation titles, tab names
    - Data sources: in-memory cache of last-loaded folders + memories (no extra API calls on keypress)
    - Keyboard: `↑`/`↓` to navigate results, `Enter` to navigate to item, `Escape` to close
    - Show result type badge (Folder / Memory / Observation) and secondary detail (agentId / timestamp)

  - [~] B1.2 Register global keydown listener
    - `document.addEventListener("keydown", ...)` — detect `Ctrl+K` on Windows/Linux, `Cmd+K` on Mac
    - Focus input field on open; re-focus last active element on close

  - [~] B1.3 Style command palette
    - Modal overlay with `backdrop-filter: blur(4px)` behind the panel
    - Max-width 640px, centered; input at top, results list below with keyboard-highlight state
    - Respect `data-theme="dark"` CSS vars; animate open/close with `transform: translateY(-8px) → 0` + `opacity`

- [ ] B2. Replace Canvas graph with D3 force-directed SVG
  - [~] B2.1 Add D3 v7 via CDN with CSP nonce
    - Load `d3.v7.min.js` via `<script nonce="__AGENTMEMORY_VIEWER_NONCE__">` inline or from self-hosted path
    - Remove the existing Canvas `<canvas>` element and hand-rolled physics loop

  - [~] B2.2 Implement D3 force simulation on SVG
    - Nodes as `<circle>` + `<text>` SVG groups; size proportional to `obsCount`
    - Three link types rendered as distinct path styles: `same-parent` (dashed `stroke-dasharray: 6,3`), `cross-ref` (solid), `agent-shared` (dotted `stroke-dasharray: 2,4`)
    - `d3.forceManyBody().strength()` tuned per node count: `-80` for < 20 nodes, `-200` for 20–100, `-400` for 100+

  - [~] B2.3 Add zoom/pan and click-to-expand
    - `d3.zoom()` for pan + scroll-to-zoom; double-click node to highlight its edges and neighbors, dim all others
    - Click on a node opens the Folders tab filtered to that `folderPath`

  - [~] B2.4 Fix graph edge label overlap bug (ROADMAP item)
    - Use `d3.forceCollide()` on label bounding boxes; set `textAnchor` dynamically based on node position relative to center
    - Labels only show at zoom > 0.6; below that, show labels only for hovered node

- [ ] B3. Implement virtual scroll on Timeline and Folders tabs
  - [~] B3.1 Build a reusable `VirtualList` helper in JS
    - Takes `container`, `items[]`, `rowHeight`, `renderItem(item) → HTMLElement`
    - Uses `IntersectionObserver` on a sentinel element at the bottom to load next page
    - Maintains a window of ~50 rendered DOM nodes, recycling off-screen rows

  - [~] B3.2 Apply `VirtualList` to Timeline tab
    - Replace current `observations.forEach(renderObsCard)` with `VirtualList`
    - Fetch first 50 observations; load next 50 on scroll near bottom via `/timeline` with `before` cursor from last item's timestamp

  - [~] B3.3 Apply `VirtualList` to Folders tab observation drill-down
    - When a folder row is expanded, load observations with the same virtual scroll pattern

- [ ] B4. First-run onboarding experience
  - [~] B4.1 Detect empty state on Dashboard tab load
    - Check `health_check` response: if `observationCount === 0` and `memoryCount === 0`, show onboarding card

  - [~] B4.2 Build onboarding card component
    - Three-step guide: (1) Configure `AGENTMEMORY_SECRET` env var, (2) Set `GEMINI_API_KEY` for semantic search, (3) Point your agent hook at `POST /agentmemory/agent/observe`
    - Each step shows a code snippet (pre-formatted, with a copy button) and a green checkmark if the feature is already active (read from `/config/flags` response)
    - Dismissable with "Got it" button; stores dismissed state in `localStorage`

  - [~] B4.3 Add "copy to clipboard" utility function
    - `copyToClipboard(text)` uses `navigator.clipboard.writeText()` with fallback to `document.execCommand("copy")`
    - Show a transient "Copied!" tooltip for 1.5 seconds after click

- [ ] B5. Add MCP Tool Tester tab
  - [~] B5.1 Add "Tools" tab to the tab bar
    - Fetch `GET /agentmemory/mcp/tools` on tab activate; render a list of all tools with name + description

  - [~] B5.2 Build tool invocation panel
    - Click a tool → show its `inputSchema` as an auto-generated JSON editor (textarea pre-filled with `{}` containing all required keys)
    - "Run" button posts to `POST /agentmemory/mcp/tools` with `{name, arguments}`
    - Response rendered as syntax-highlighted JSON below the editor; error state shown in red

  - [~] B5.3 Add request/response history for the session
    - Keep last 10 tool calls in memory; render as collapsible history entries above the editor
    - Each history entry shows tool name, timestamp, and success/error badge

- [ ] B6. Viewer polish and accessibility
  - [~] B6.1 Fix dark mode inconsistencies
    - Audit all components against `data-theme="dark"` CSS vars; find and fix the graph tooltip and graph controls that don't fully respond to theme changes
    - Test in both themes: all text must meet WCAG AA contrast ratio (4.5:1 for body, 3:1 for large text)

  - [~] B6.2 Add keyboard navigation for tabs
    - `Tab` key cycles through tab buttons; `Enter`/`Space` activates; `←`/`→` arrow keys move between tabs when a tab has focus
    - Add `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` ARIA attributes to the tab bar

  - [~] B6.3 Add loading skeleton states
    - Replace `<div class="loading">Loading...</div>` with animated skeleton placeholder cards that match the shape of the real content
    - Use CSS `@keyframes shimmer` with a `background: linear-gradient(90deg, ...)` animation

---

## Track C — Reliability & Testing

> Owner: Backend / QA Dev
> Goal: Close the test coverage gap and harden the system against bad inputs and restarts.
> Priority: HIGH — required before any production deployment.

- [ ] C1. Write tests for the core observe/remember/search/context pipeline
  - [~] C1.1 Create `tests/test_observe_core.py`
    - Test `observe()` with valid payload → returns obs dict with correct fields
    - Test `observe()` with missing `sessionId` / `hookType` / `timestamp` → raises `ValueError`
    - Test that `strip_private_data()` fires and `[REDACTED_SECRET]` appears in stored obs
    - Test that `MAX_OBS_PER_SESSION` env cap raises `ValueError` when exceeded

  - [~] C1.2 Create `tests/test_remember.py`
    - Test `remember()` creates a new memory with `isLatest=True`
    - Test that a second `remember()` with > 0.7 Jaccard similarity sets `isLatest=False` on the old memory and links via `parentId`
    - Test that a second `remember()` with < 0.7 similarity creates an independent second memory

  - [~] C1.3 Create `tests/test_search.py`
    - Test `SearchIndex.add()` + `SearchIndex.search()` returns the added document at rank 1 for an exact-match query
    - Test prefix matching: index `"authentication"`, search `"authen"` → doc appears in results
    - Test synonym expansion: index `"database connection"`, search `"db conn"` → doc appears via synonym map
    - Test `HybridSearch.search()` in BM25-only mode (no embedding provider) returns same results as `SearchIndex.search()`

  - [~] C1.4 Create `tests/test_context.py`
    - Test `context()` respects `TOKEN_BUDGET` — returned context string is under budget
    - Test that pinned slot content appears first in context output
    - Test that empty DB returns a well-formed but minimal context dict

- [ ] C2. Write Hypothesis property tests
  - [~] C2.1 Create `tests/test_properties.py` with all 8 property tests
    - **Property 1: Pair Isolation** — two distinct `(folderPath, agentId)` pairs never share observations
    - **Property 2: Observation Count Consistency** — `meta.obsCount == len(kv.list(folder_obs_scope))`
    - **Property 3: Index Coverage** — every written pair has a `KV.folders` entry
    - **Property 4: Privacy Invariant** — no stored obs text matches any secret regex pattern after `folder_observe()`
    - **Property 5: Timeline Ordering** — `folder_timeline()` always returns results sorted newest-first
    - **Property 6: Forget Completeness** — after `forget({folderPath, agentId})`, all three scopes are empty and BM25 has no entry for any obs from that pair
    - **Property 7: Memory Version Uniqueness** — only one memory per concept cluster has `isLatest=True`
    - **Property 8: Path Normalization Idempotency** — `normalize(normalize(p)) == normalize(p)` for all valid inputs
    - Use `tmp_path` pytest fixture for isolated SQLite DB per test; `@settings(max_examples=50)`

- [ ] C3. Add integration tests for REST endpoints
  - [~] C3.1 Create `tests/test_api.py` using Flask test client
    - Test `POST /agentmemory/agent/observe` → 201 with obs id
    - Test `POST /agentmemory/agent/observe` missing `folderPath` → 400
    - Test `POST /agentmemory/search` with query → 200 with results list
    - Test `GET /agentmemory/folders` → 200 with folders list
    - Test `GET /agentmemory/health` → 200 with `folderCount`, `observationCount` keys
    - Test `GET /agentmemory/livez` → 200 without auth (open endpoint)
    - Test authenticated endpoints return 401 when `AGENTMEMORY_SECRET` is set but token is wrong

- [ ] C4. Improve HuggingFace sync reliability
  - [~] C4.1 Replace mtime-based sync trigger with audit log high-water mark
    - On each sync iteration, read `MAX(ts)` from `audit_log`; compare to last-synced high-water mark stored in a local `.sync_state` file
    - Only upload if `current_max_ts > last_synced_ts`; update the watermark after successful upload
    - This eliminates false-positive uploads caused by WAL mode mtime changes

  - [~] C4.2 Add sync status to the `/health` endpoint
    - Report `last_sync_at` (ISO timestamp of last successful HF upload), `sync_status` (`"ok"` / `"never"` / `"error"`), and `db_size_bytes`
    - Read from `.sync_state` file; if file doesn't exist, report `"never"`

- [ ] C5. Add graceful shutdown handler
  - [~] C5.1 Register `SIGTERM` and `SIGINT` handlers in `src/workers.py`
    - On signal: set a global `_shutting_down` flag; wait for any in-flight `schedule_save()` debounce timer to fire; call `persistence.save()` synchronously; log shutdown message
    - Exit cleanly with code 0

---

## Track D — Integrations & Developer Experience

> Owner: DevEx / Infra Dev
> Goal: Make agentmemory easier to install, configure, and connect to agents.
> Priority: MEDIUM — high value but not blocking.

- [ ] D1. Clean up dead code and scratch files
  - [~] D1.1 Delete scratch and personal utility files
    - Remove `scratch_diff.txt`, `scratch_test_import.py` from repo root
    - Remove `push_local_data.py` and `push_second_brain.py` — these are personal sync scripts, not library code
    - Remove `migrate_dolt_to_sqlite.py` — the migration is done; archive it in `docs/migration/` if it needs to be kept for reference

  - [~] D1.2 Remove all 410-stub route handlers from `app.py`
    - Delete route functions for: `api_session_start`, `api_session_end`, `api_session_commit`, `api_session_by_commit`, `api_sessions`, `api_replay_sessions`, `api_antigravity_sync`, `api_lessons_list`, `api_lessons_save`, `api_lessons_search`, `api_lessons_strengthen`, `api_slots_list`, `api_slots_get`, `api_slots_create`, `api_slots_append`, `api_slots_replace`, `api_slots_delete`, `api_slots_reflect`, `api_timeline_legacy`
    - These return 410 and serve no purpose except confusion; the routes are already documented as removed

  - [~] D1.3 Fix dead references in `mcp_stdio.py`
    - `perform_antigravity_sync_all_local()` calls `/summarize` and `/slot/reflect` which are 410 — update or remove these calls
    - The Antigravity sync logic (both functions) is personal/business-specific; move to `examples/antigravity_sync.py` and remove from the generic MCP bridge
    - Clean up the double-import of `os`, `json`, `requests` inside `perform_antigravity_sync_local()`

  - [~] D1.4 Move `DESIGN.md` and clarify its purpose
    - Rename to `docs/viewer-design-system.md` — the current name `DESIGN.md` implies system architecture
    - Create a real `ARCHITECTURE.md` at the repo root that documents the Flask blueprint structure, module responsibilities, KV scope layout, and data flow (observe → BM25/vector → WebSocket → viewer)

- [ ] D2. Fix CORS and security hardening
  - [~] D2.1 Move CORS allowlist to configuration
    - Replace the `after_request` string-matching logic with a configurable `AGENTMEMORY_CORS_ORIGINS` env var (comma-separated)
    - Default to `http://localhost:*,http://127.0.0.1:*,vscode-webview://*` — still localhost-only but driven by config
    - Add `chrome-extension://` support as an explicit opt-in via env var, not hardcoded

  - [~] D2.2 Stop injecting `AGENTMEMORY_SECRET` into HTML template
    - The current `__AGENTMEMORY_AUTO_TOKEN__` replacement embeds the raw secret in the page source
    - Replace with a session-scoped viewer token: on page load, the server issues a short-lived viewer token (signed HMAC of `secret + timestamp`, valid 1h) via a `Set-Cookie` header
    - The viewer uses this cookie for API calls; the actual `AGENTMEMORY_SECRET` never touches the DOM

- [ ] D3. Build prebuilt agent hook scripts
  - [~] D3.1 Create `hooks/claude-code-hook.sh` for Claude Code
    - Bash script that posts to `POST /agentmemory/agent/observe` on each tool use
    - Reads `AGENTMEMORY_URL` and `AGENTMEMORY_SECRET` from environment
    - Include `folderPath` from `$PWD`, `agentId` from `$CLAUDE_AGENT_ID` or `"claude-code"`

  - [~] D3.2 Create `hooks/cursor-hook.js` for Cursor
    - JavaScript snippet for Cursor's `.cursorrules` hook system
    - Same endpoint and payload structure as the bash version

  - [~] D3.3 Create `hooks/powershell-hook.ps1` for terminal sessions
    - Formalize and generalize the hook currently documented in `agentmemory.md`
    - Parameterize `$ServerUrl` and `$AgentId`; ship as a ready-to-source snippet

  - [~] D3.4 Update `INSTALL_FOR_AGENTS.md` with hook setup instructions
    - One section per agent type; link to the corresponding hook script in `hooks/`
    - Include the `.env` file format and `AGENTMEMORY_SECRET` setup steps

- [ ] D4. Build `pip`-installable package
  - [~] D4.1 Add `pyproject.toml`
    - Package name: `agentmemory`; entry point: `agentmemory = src.app:main`
    - Include `src/viewer/` as package data
    - Python `>=3.10` requirement

  - [~] D4.2 Add CLI entrypoint
    - `agentmemory serve [--port PORT] [--host HOST]` — starts the Flask server
    - `agentmemory migrate` — runs `migrate_sessions_to_folders()` and prints summary
    - `agentmemory export [--output FILE]` — runs `export_data()` and writes JSON

  - [~] D4.3 Update GitHub Actions CI workflow
    - Add steps: `pip install -e .[dev]`, `pytest tests/ -v`, `pip install build; python -m build`
    - Run on `push` to `main` and `pull_request`

- [ ] D5. Additional embedding provider support
  - [~] D5.1 Add `OpenAIEmbeddingProvider` class to `src/search.py`
    - Mirror the `GeminiEmbeddingProvider` interface: `embed(text)`, `embed_batch(texts)`, `dimensions` (1536 for `text-embedding-3-small`)
    - Use `urllib.request` (no new dependencies); read API key from `OPENAI_API_KEY` env var

  - [~] D5.2 Add local `sentence-transformers` provider (optional install)
    - `SentenceTransformerProvider` class; lazy-import `sentence_transformers`; log a clear error if not installed
    - Default model: `all-MiniLM-L6-v2` (384 dims); configurable via `AGENTMEMORY_LOCAL_EMBEDDING_MODEL` env var

  - [~] D5.3 Auto-select embedding provider in `init_app()`
    - Priority: `GEMINI_API_KEY` → `OPENAI_API_KEY` → `AGENTMEMORY_LOCAL_EMBEDDING_MODEL` → BM25-only
    - Log which provider was selected on startup

---

## Notes

- **Track A must be completed before any other backend feature work.** The blueprint split is the prerequisite that makes parallel development safe. Do not add new routes to the monolithic `app.py`.
- **Track B and Track C can run in parallel** with Track A. The viewer and test suite touch different files.
- **Track D tasks D1.1–D1.3 (cleanup) can be done immediately** — they are deletions and do not require Track A to finish first.
- All new routes follow the existing auth pattern: call `check_auth()` at the top; never skip it.
- All new viewer JS follows the existing CSP constraint: no inline event handlers, all scripts nonce-tagged.
- Property tests in `tests/test_properties.py` (C2.1) are the authoritative source; unit tests in C1 are complementary, not replacements.
- The `D2.2` security fix (token injection) should be shipped before any public/shared deployment.

---

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "label": "Cleanup (no dependencies)",
      "tasks": ["D1.1", "D1.2", "D1.3", "D1.4"]
    },
    {
      "id": 1,
      "label": "Structural foundation",
      "tasks": ["A1.1", "A2.3", "A3.1", "C1.1", "C1.2", "C1.3", "B4.1", "B4.2"]
    },
    {
      "id": 2,
      "label": "Module split + UX foundation",
      "tasks": ["A1.2", "A2.1", "A2.2", "A3.2", "A4.1", "B1.1", "B2.1", "B3.1", "C1.4", "D2.1"]
    },
    {
      "id": 3,
      "label": "Blueprint validation + UX components",
      "tasks": ["A1.3", "A3.3", "A4.2", "A4.3", "B1.2", "B1.3", "B2.2", "B3.2", "B3.3", "C2.1", "C3.1", "D2.2"]
    },
    {
      "id": 4,
      "label": "Advanced features",
      "tasks": ["B2.3", "B2.4", "B4.3", "B5.1", "B5.2", "C4.1", "C4.2", "C5.1", "D3.1", "D3.2", "D3.3", "D5.1"]
    },
    {
      "id": 5,
      "label": "Polish + packaging",
      "tasks": ["B5.3", "B6.1", "B6.2", "B6.3", "D3.4", "D4.1", "D4.2", "D4.3", "D5.2", "D5.3"]
    }
  ]
}
```
