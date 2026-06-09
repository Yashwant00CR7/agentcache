# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Python reimplementation of the agentmemory persistent memory server. Exposes a REST API + WebSocket stream + MCP tools endpoint that AI coding agents use to store and retrieve session observations, long-term memories, lessons, and pinned memory slots. Backed by a Dolt SQL Server (MySQL-compatible).

## Running

**Prerequisite**: Dolt SQL Server must be running on `127.0.0.1:3306` with a database named `agentmemory`. Config is read from `~/.agentmemory/.env` at startup.

```bash
# Start the memory API server and built-in viewer (port 3111)
python src/app.py
```

The built-in HTML dashboard is accessible at:
- `http://localhost:3111/viewer` or `http://localhost:3111/`

No build step. No test runner is configured yet.

## Key Environment Variables

Set in `~/.agentmemory/.env` or as system env vars:

| Variable | Default | Purpose |
|---|---|---|
| `III_REST_PORT` / `PORT` | `3111` | API server port |
| `DOLT_HOST/PORT/USER/PASSWORD/DATABASE` | `127.0.0.1/3306/root//"agentmemory"` | Dolt connection |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Enables 768-dim vector search; without it, BM25-only |
| `AGENTMEMORY_SECRET` | — | Enables Bearer token auth on all endpoints |
| `AGENT_ID` | — | Default agent ID for scope isolation |
| `AGENTMEMORY_AGENT_SCOPE=isolated` | — | Filters sessions/obs to current `AGENT_ID` |
| `DOLT_AUTO_COMMIT=false` | auto-commit | Disable Dolt versioning commits per write |
| `MAX_OBS_PER_SESSION` | `500` | Hard cap on observations per session |
| `TOKEN_BUDGET` | `2000` | Max tokens in compiled context response |
| `GRAPH_EXTRACTION_ENABLED=true` | `false` | Knowledge graph extraction (requires LLM) — **disabled by default** |
| `CONSOLIDATION_ENABLED=true` | `false` | Memory consolidation (requires LLM) — **disabled by default** |
| `AGENTMEMORY_AUTO_COMPRESS=true` | `false` | LLM-powered observation compression |

## Architecture

### `src/db.py` — Storage Layer
`StateKV` wraps a single Dolt table `kv_store(scope VARCHAR, key VARCHAR, value LONGTEXT)`. All data is JSON-serialized. Scopes are namespaced strings (e.g. `mem:sessions`, `mem:obs:{session_id}`). Dolt versioning is triggered via `CALL dolt_add('-A')` + `CALL dolt_commit(...)` stored procedures — this is what makes the store git-versioned.

### `src/functions.py` — Business Logic
All core operations live here. Important globals:
- `_bm25_index` / `_vector_index` — in-memory search indexes (rebuilt from DB on startup if empty)
- `_hybrid_search` — combines BM25 + vector search; only initialized when embedding provider is set
- `_stream_broadcaster` — WebSocket broadcast callback injected by `app.py`

Key scopes are defined in the `KV` class. Dynamic scopes: `KV.observations(session_id)` → `mem:obs:{session_id}`.

**Observation pipeline**: raw payload → `strip_private_data()` → `build_synthetic_compression()` → stored + BM25-indexed + vector-indexed + Dolt-committed + WebSocket-broadcast.

**Memory versioning**: `remember()` checks Jaccard similarity against existing memories; if > 0.7 match found, the new memory supersedes the old one (`isLatest=False` on old, `parentId` set on new).

**Context compilation** (`context()`): assembles pinned slots → project profile → lessons (scored by confidence × project match) → past session summaries, capped at `TOKEN_BUDGET` tokens (estimated at `len/3`).

**Lessons**: fingerprinted by SHA-256 of content. Duplicate saves strengthen confidence (`+0.1 × (1 - conf)`). Weekly decay sweep reduces confidence by `decayRate × weeks`; soft-deleted at ≤ 0.1 confidence with 0 reinforcements.

### `src/search.py` — Search Indexes
- `SearchIndex`: BM25 with custom Porter stemmer. Persisted to Dolt in sharded 2MB chunks via `IndexPersistence`.
- `VectorIndex`: cosine similarity over Gemini 768-dim embeddings stored as base64-encoded float32 arrays.
- `HybridSearch`: fuses BM25 + vector scores with RRF (reciprocal rank fusion).

### `src/app.py` — Flask API
Initializes DB → embedding provider → index persistence → rebuilds index if empty (background thread). All endpoints check `AGENTMEMORY_SECRET` via timing-safe Bearer token comparison. WebSocket at `/stream/mem-live/viewer` broadcasts raw + compressed observations to connected viewers.

MCP tools are served at `GET /agentmemory/mcp/tools` (schema list) and `POST /agentmemory/mcp/tools` (tool call dispatch).

### `src/viewer/index.html` — Built-in HTML Dashboard
Interactive web dashboard served directly by the Flask server. Provides real-time view of active sessions, timelines, memories with search, slots editor, and DB migration panel. Connects to the Flask backend via REST and live WebSockets. Imports legacy TypeScript `.bin` files via `src/import_data.py`.

## API Surface

Base path: `/agentmemory/`

- `GET /livez` — health/liveness (no auth)
- `POST /observe` — ingest a hook event observation
- `POST /agent/observe` — simplified observe for direct agent use
- `POST /remember` / `POST /agent/remember` — save long-term memory
- `POST /forget` — delete memory/session/observations
- `POST /context` — compile context for a session+project
- `POST /search` — hybrid BM25+vector search
- `POST/GET /lessons` — lessons CRUD + `/lessons/search`, `/lessons/strengthen`
- `GET/POST /slots`, `GET/POST/DELETE /slot` — memory slots CRUD
- `POST /slot/reflect` — auto-populate slots from session observations
- `POST/GET /session/start|end|commit` — session lifecycle
- `GET /sessions`, `GET /observations` — list data
- `GET/POST /relations` — knowledge graph edges
- `POST /evolve` — create new memory version
- `POST /timeline` — chronological observation window
- `GET /profile` — project profile (top concepts/files); no `?project` → returns list of all known projects
- `GET /actions` — list actions (`?limit`, `?status`)
- `POST /actions` — create action
- `PATCH /actions/<id>` — update action status/fields
- `GET /frontier` — pending+active actions sorted by priority
- `GET /insights` — list insights (`?limit`)
- `GET /replay/sessions` — sessions list for replay tab
- `GET /replay/load?sessionId=<id>` — full session + observations for replay
- `GET/POST /mcp/tools` — MCP protocol adapter
