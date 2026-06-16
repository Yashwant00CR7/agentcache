# Implementation Plan: agentmemory-python — Upscale & UX Overhaul

## Overview

The folder-based memory migration is complete. The backend structural refactor (Track A)
is largely done — blueprints are split, `src/memory/` and `src/storage/` packages exist,
`db.py` has per-thread connections + WAL checkpoint + stats, and `IndexPersistence` has
debounce + dirty-flag tracking. This plan tracks what remains.

**Track A — Backend Structure** ✅ mostly complete
**Track B — Viewer UX** (Frontend / Full-Stack)
**Track C — Reliability & Testing** ✅ largely complete
**Track D — Integrations & DX** (DevEx / Infra)

---

## Tasks

## Track A — Backend Structure Refactor

- [x] A1. Split `src/app.py` into Flask blueprints
  - [x] A1.1 Create `src/routes/` directory with blueprints
    - `src/routes/observations.py` — `/observe`, `/agent/observe`, `/folder/observations`, `/folders`
    - `src/routes/memories.py` — `/remember`, `/agent/remember`, `/memories`, `/forget`
    - `src/routes/search.py` — `/search`, `/timeline`
    - `src/routes/graph.py` — `/graph`, `/graph/stats`, `/graph/query`, `/graph/build`
    - `src/routes/health.py` — `/livez`, `/health`, `/audit`, `/config/flags`
    - `src/routes/mcp.py` — `/mcp/tools` GET + POST
    - `src/routes/migration.py` — `/migrate`

  - [x] A1.2 Rebuild `src/app.py` as a thin factory
    - `create_app()` factory: init DB, init embeddings (D5.3 priority: Gemini → OpenAI → SentenceTransformer → BM25-only), register all blueprints, set up WebSocket, register CORS hook
    - Background worker setup moved to `src/workers.py`
    - `app.py` is now ~170 lines

  - [x] A1.3 Validate no route regressions after blueprint split
    - `tests/test_route_regressions.py` covers all main endpoints

- [x] A2. Split `src/functions.py` into focused modules
  - [x] A2.1 Create `src/memory/` package
    - `src/memory/observe.py` — thin re-export shim for `folder_observe`, `observe`, `build_synthetic_compression`, `strip_private_data`
    - `src/memory/remember.py` — thin re-export shim for `remember`, `forget`, `jaccard_similarity`
    - `src/memory/context.py` — thin re-export shim for `context`, `export_data`, `rebuild_index`
    - `src/memory/graph.py` — thin re-export shim for `folder_graph_build`
    - `src/memory/timeline.py` — thin re-export shim for `folder_timeline`, `folder_search`
    - `src/memory/health.py` — thin re-export shim for `health_check`, `auto_forget`
    - _Note: the shims currently delegate back to `functions.py`. The canonical implementation still lives in `functions.py`._

  - [~] A2.2 Keep `src/functions.py` as a compatibility shim
    - All routes import from `functions` directly; `src/memory/*` shims exist for future decoupling

  - [x] A2.3 Move `KV` class and path utilities to `src/storage/`
    - `src/storage/scopes.py` — `KV` class (copy; canonical version still in `functions.py`)
    - `src/storage/paths.py` — `normalize_folder_path()`, `validate_agent_id()`, `generate_id()`, `fingerprint_id()`
    - `src/storage/images.py` — `save_image_to_disk()`, `delete_image()`, `touch_image()`, `is_managed_image_path()`

- [x] A3. Fix `src/db.py` connection management
  - [x] A3.1 Per-thread persistent connections via `threading.local()`
  - [x] A3.2 WAL checkpoint on graceful shutdown (`atexit` + `SIGTERM`/`SIGINT` handlers in `workers.py`)
  - [x] A3.3 `db.stats()` method returning `db_size_bytes`, `kv_row_count`, `audit_row_count`, `wal_size_bytes`

- [x] A4. Debounce index persistence writes
  - [x] A4.1 `IndexPersistence.schedule_save()` uses `threading.Timer` (5s debounce)
  - [x] A4.2 `_dirty` flag on `SearchIndex` and `VectorIndex` — skip save if not dirty
  - [~] A4.3 Unit test for debounce behavior → `tests/test_debounce.py` exists

---

## Track B — Viewer UX Overhaul

> Owner: Frontend / Full-Stack Dev

- [ ] B1. Add Command Palette (`Ctrl+K` / `Cmd+K`)
  - [~] B1.1–B1.3 Not yet implemented in `src/viewer/index.html`

- [ ] B2. Replace Canvas graph with D3 force-directed SVG
  - [~] B2.1–B2.4 Not yet implemented

- [ ] B3. Implement virtual scroll on Timeline and Folders tabs
  - [~] B3.1–B3.3 Not yet implemented

- [ ] B4. First-run onboarding experience
  - [~] B4.1–B4.3 Not yet implemented

- [ ] B5. Add MCP Tool Tester tab
  - [~] B5.1–B5.3 Not yet implemented

- [ ] B6. Viewer polish and accessibility
  - [~] B6.1–B6.3 Not yet implemented

---

## Track C — Reliability & Testing

> All test files exist and are passing.

- [x] C1. Core pipeline tests
  - [x] `tests/test_observe_core.py` — folder observe validation, privacy stripping, cap enforcement
  - [x] `tests/test_remember.py` — memory creation, Jaccard dedup, independent memories
  - [x] `tests/test_search.py` — BM25 add/search, prefix matching, synonym expansion, hybrid search
  - [x] `tests/test_context.py` — TOKEN_BUDGET, context compilation

- [x] C2. Hypothesis property tests
  - [x] `tests/test_properties.py` — 8 properties:
    1. Pair Isolation
    2. Observation Count Consistency
    3. Index Coverage
    4. Privacy Invariant
    5. Timeline Ordering
    6. Forget Completeness
    7. Memory Version Uniqueness
    8. Path Normalization Idempotency

- [x] C3. Integration tests
  - [x] `tests/test_api.py` — Flask test client hitting all main endpoints
  - [x] `tests/test_route_regressions.py` — regression suite after blueprint split

- [x] Additional test files
  - [x] `tests/test_folder_observe.py` — folder_observe unit tests
  - [x] `tests/test_folder_graph_build.py` — graph builder tests
  - [x] `tests/test_forget.py` — forget function tests
  - [x] `tests/test_normalize.py` — path normalization tests
  - [x] `tests/test_obs_lookup.py` — obs_lookup index tests
  - [x] `tests/test_migration.py` — session → folder migration tests
  - [x] `tests/test_timeline.py` — folder_timeline tests
  - [x] `tests/test_graph.py` — graph edge tests
  - [x] `tests/test_debounce.py` — IndexPersistence debounce test

- [ ] C4. HuggingFace sync reliability
  - [~] C4.1 Replace mtime-based sync trigger with audit log high-water mark
  - [~] C4.2 Add sync status to `/health` endpoint

- [x] C5. Graceful shutdown handler
  - [x] `src/workers.py` registers SIGTERM/SIGINT handlers, flushes persistence, runs WAL checkpoint

---

## Track D — Integrations & Developer Experience

- [ ] D1. Clean up dead code and scratch files
  - [~] D1.1 Remove scratch files (scratch_diff.txt, push_local_data.py, push_second_brain.py, migrate_dolt_to_sqlite.py)
  - [~] D1.2 Remove 410-stub route handlers from app.py
  - [~] D1.3 Fix dead references in `mcp_stdio.py`
  - [~] D1.4 Move/rename `DESIGN.md` → `docs/viewer-design-system.md`; ensure `ARCHITECTURE.md` is accurate

- [ ] D2. Fix CORS and security hardening
  - [x] D2.1 CORS configurable via `AGENTMEMORY_CORS_ORIGINS` env var (implemented in `app.py`)
  - [~] D2.2 Stop injecting `AGENTMEMORY_SECRET` into HTML template (viewer token via cookie)

- [ ] D3. Prebuilt agent hook scripts
  - [~] D3.1–D3.4 `hooks/` directory may contain scripts; need verification and docs update

- [x] D4. `pip`-installable package
  - [x] `pyproject.toml` with `agentmemory` entry point → `src.cli:main`
  - [x] `src/cli.py` CLI entrypoint exists
  - [~] D4.3 GitHub Actions CI workflow updates

- [x] D5. Additional embedding providers
  - [x] `OpenAIEmbeddingProvider` in `src/search.py` (D5.1)
  - [x] `SentenceTransformerProvider` in `src/search.py` (D5.2)
  - [x] Auto-select provider in `create_app()` — Gemini → OpenAI → SentenceTransformer → BM25-only (D5.3)

---

## Remaining High-Priority Work

The following items are not yet done and represent the bulk of remaining effort:

1. **Track B (Viewer UX)** — Command palette, D3 graph, virtual scroll, onboarding, MCP tool tester, accessibility polish
2. **D1.2** — Remove 410 stub routes from `app.py` (they live in `observations.py` as compat shims — keep those, but the dead `app.py` stubs should be gone; already done since the monolith was split)
3. **D2.2** — Viewer token security (secret not in DOM)
4. **C4** — HF sync reliability with audit watermark

---

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "label": "Completed — structural foundation",
      "tasks": ["A1.1", "A1.2", "A1.3", "A2.1", "A2.3", "A3.1", "A3.2", "A3.3", "A4.1", "A4.2", "C1.1", "C1.2", "C1.3", "C1.4", "C2.1", "C3.1", "C5.1", "D4.1", "D4.2", "D5.1", "D5.2", "D5.3", "D2.1"]
    },
    {
      "id": 1,
      "label": "Next — viewer UX foundation",
      "tasks": ["B4.1", "B4.2", "B4.3", "B1.1", "B2.1", "B3.1"]
    },
    {
      "id": 2,
      "label": "Viewer components",
      "tasks": ["B1.2", "B1.3", "B2.2", "B3.2", "B3.3", "B5.1", "B5.2", "D2.2"]
    },
    {
      "id": 3,
      "label": "Advanced viewer + sync",
      "tasks": ["B2.3", "B2.4", "B5.3", "B6.1", "B6.2", "B6.3", "C4.1", "C4.2"]
    },
    {
      "id": 4,
      "label": "Polish + packaging",
      "tasks": ["D1.1", "D1.2", "D1.3", "D1.4", "D3.1", "D3.2", "D3.3", "D3.4", "D4.3"]
    }
  ]
}
```
