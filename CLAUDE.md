# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python REST + WebSocket + MCP memory server backed by SQLite. No Node.js, no Dolt. Agents use it to store observations scoped to `(folder_path, agent_id)` pairs and global long-term memories. The architecture is **folder-based** — sessions, lessons, slots, and actions are removed.

## Running

```bash
pip install -r requirements.txt
python src/app.py
# Server on http://localhost:3111
# Viewer at http://localhost:3111/viewer
```

No build step. SQLite file lives at `~/.agentcache/agentcache.db`. Config optionally loaded from `~/.agentcache/.env`.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `III_REST_PORT` / `PORT` | `3111` | API server port |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Gemini vector search (priority 1) |
| `OPENAI_API_KEY` | — | OpenAI vector search (priority 2) |
| `AGENTCACHE_LOCAL_EMBEDDING_MODEL` | — | SentenceTransformer model (priority 3) |
| `AGENTCACHE_SECRET` | — | Bearer token auth on all endpoints |
| `AGENT_ID` | — | Default agent ID |
| `AGENTCACHE_AGENT_SCOPE=isolated` | — | Filter data to current `AGENT_ID` |
| `AGENTCACHE_CWD` | — | Fallback folder path for legacy clients |
| `MAX_OBS_PER_FOLDER` | `2000` | Hard cap on observations per (folder, agent) pair |
| `TOKEN_BUDGET` | `2000` | Context compilation cap |
| `GRAPH_EXTRACTION_ENABLED` | `false` | Knowledge graph extraction (needs LLM) |
| `CONSOLIDATION_ENABLED` | `false` | Memory consolidation (needs LLM) |
| `AGENTCACHE_AUTO_COMPRESS` | `false` | LLM-powered observation compression |
| `AGENTCACHE_CORS_ORIGINS` | see app.py | Comma-separated allowed CORS origins |

## Architecture

### `src/db.py` — Storage Layer

`StateKV` wraps SQLite with two tables:
- `kv_store(scope, key, value)` — all data as JSON, namespaced by scope
- `audit_log(id, ts, agent_id, message)` — write audit trail

Per-thread persistent connections via `threading.local()`. WAL checkpoint on shutdown.

Key scopes: `mem:folders` (index), `mem:folder:{path}:{agent}` (observations), `mem:foldermeta:{path}:{agent}` (metadata), `mem:obs_lookup` (O(1) reverse lookup), `mem:memories` (global), `mem:index:bm25:*` (search).

### `src/functions.py` — Business Logic

All core implementations. Key functions:
- `folder_observe(kv, payload)` — ingest a folder-scoped observation
- `folder_search(kv, query, limit, folder_path, agent_id)` — BM25+vector hybrid search
- `folder_timeline(kv, limit, folder_path, agent_id, before, after)` — activity feed
- `folder_graph_build(kv)` — build graph nodes + edges
- `remember(kv, data)` / `forget(kv, data)` — global memory management
- `health_check(kv)` — system stats
- `export_data(kv, data)` — v2 JSON export
- `migrate_sessions_to_folders(kv, dry_run)` — legacy migration

`src/memory/*` contains thin shim modules that re-export from here.

### `src/search.py` — Search Indexes

- `SearchIndex`: BM25 with Porter stemmer + synonym expansion. `_dirty` flag prevents unnecessary saves.
- `VectorIndex`: cosine similarity over embeddings as base64 float32. `_dirty` flag.
- `HybridSearch`: RRF fusion (k=60) of BM25 + vector.
- `GeminiEmbeddingProvider`, `OpenAIEmbeddingProvider`, `SentenceTransformerProvider`.

### `src/app.py` — Flask Factory

`create_app()`: init DB → auto-select embedding provider → init IndexPersistence → backfill obs_lookup → register blueprints → setup WebSocket → register CORS → start background workers.

### `src/routes/` — Flask Blueprints

| Blueprint | Endpoints |
|-----------|-----------|
| `observations.py` | `/observe`, `/agent/observe`, `/folders`, `/folder/observations`, legacy session shims |
| `memories.py` | `/remember`, `/agent/remember`, `/memories`, `/forget` |
| `search.py` | `/search`, `/timeline` |
| `graph.py` | `/graph`, `/graph/stats`, `/graph/query`, `/graph/build` |
| `health.py` | `/livez`, `/health`, `/audit`, `/config/flags` |
| `mcp.py` | `/mcp/tools` GET+POST (12 tools) |
| `migration.py` | `/migrate` |

### `src/workers.py` — Background Threads

- Index rebuild thread (if BM25 index is empty or out of sync on boot)
- Auto-forget sweep loop (hourly)
- SIGTERM/SIGINT handlers: flush `IndexPersistence`, WAL checkpoint, exit 0

### `src/viewer/index.html` — Dashboard

Single-file HTML, served at `/viewer`. Tabs: **Folders**, **Memories**, **Graph**, **Timeline**. No build step.

## MCP Tools (12 active)

`agent_observe`, `agent_remember`, `memory_recall`, `memory_smart_search`, `memory_save`, `memory_export`, `memory_forget`, `memory_diagnose`, `memory_folders`, `memory_folder_observations`, `memory_timeline`

Full schema at `GET /agentcache/mcp/tools`.
