# agentcache-python — Agent Instructions

## What this project is

A Python REST + WebSocket + MCP memory server backed by SQLite. No Node.js, no iii-engine, no Dolt. Agents use it to store observations scoped to `(folder_path, agent_id)` pairs and global long-term memories. The viewer, MCP tools, and REST API are built around the **folder-based memory model** — sessions, lessons, slots, and actions are removed.

## Project layout

```
src/
├── app.py              Thin Flask factory — create_app(), WebSocket, CORS hook
├── cli.py              CLI entrypoint (agentcache serve/migrate/export)
├── connect.py          Client connection helper
├── db.py               StateKV — SQLite WAL, per-thread connections, stats()
├── functions.py        All canonical business logic (large; memory/ shims delegate here)
├── search.py           BM25 + VectorIndex + HybridSearch + 3 embedding providers
├── viewer_helpers.py   Viewer HTML injection helper
├── workers.py          Background threads — index rebuild, auto-forget, SIGTERM handler
│
├── routes/             Flask blueprints
│   ├── observations.py   /observe, /agent/observe, /folders, /folder/observations
│   ├── memories.py       /remember, /agent/remember, /memories, /forget
│   ├── search.py         /search, /timeline
│   ├── graph.py          /graph, /graph/stats, /graph/query, /graph/build
│   ├── health.py         /livez, /health, /audit, /config/flags
│   ├── mcp.py            /mcp/tools GET+POST (12 active tools)
│   └── migration.py      /migrate
│
├── memory/             Thin shim package — delegates to functions.py
│   ├── observe.py        folder_observe, observe, build_synthetic_compression, strip_private_data
│   ├── remember.py       remember, forget, jaccard_similarity
│   ├── context.py        context, export_data, rebuild_index
│   ├── graph.py          folder_graph_build
│   ├── timeline.py       folder_timeline, folder_search
│   └── health.py         health_check, auto_forget
│
├── storage/            KV scope registry + path/ID utilities
│   ├── scopes.py         KV class (mirrored from functions.py)
│   ├── paths.py          normalize_folder_path, validate_agent_id, generate_id, fingerprint_id
│   └── images.py         save_image_to_disk, delete_image, touch_image
│
└── viewer/
    └── index.html      Single-file HTML dashboard (4 tabs: Folders / Memories / Graph / Timeline)

sync.py             HuggingFace dataset backup/restore
Dockerfile          HF Space container definition
start.sh            Boot: restore DB → start server → start sync loop
requirements.txt    flask, flask-sock, requests, websockets, python-dateutil, huggingface_hub
pyproject.toml      pip-installable package (agentcache==0.9.8, Python ≥3.10)
tests/              pytest suite — unit, integration, and Hypothesis property tests
```

## Running

```bash
pip install -r requirements.txt
python src/app.py
# Server on http://localhost:3111
# Viewer at http://localhost:3111/viewer
```

No build step. No external database. SQLite file lives at `~/.agentcache/agentcache.db`.

## Architecture

### Data Model — Folder-Based Memory

The primary unit of storage is `(folder_path, agent_id)`. Each agent accumulates observations scoped to the folder it is working in. Global long-term memories remain unchanged.

### Storage — `src/db.py`

`StateKV` wraps a single SQLite file with:
- `kv_store(scope TEXT, key TEXT, value TEXT, PRIMARY KEY(scope, key))` — all data as JSON
- `audit_log(id, ts, agent_id, message)` — write audit trail
- `sync_state_metadata(key, value)` — HuggingFace sync watermark

Per-thread persistent connections via `threading.local()`. WAL checkpoint registered via `atexit` and on `SIGTERM`/`SIGINT`.

Key scopes (defined in `functions.py` `KV` class and mirrored in `src/storage/scopes.py`):

| Scope | Content |
|-------|---------|
| `mem:folders` | Index of all `(folder_path, agent_id)` pairs — key = `"{path}:{agent}"` |
| `mem:folder:{path}:{agent}` | Observations for a pair — key = obs_id |
| `mem:foldermeta:{path}:{agent}` | Metadata for a pair (obsCount, lastUpdated, summary) |
| `mem:obs_lookup` | O(1) reverse-lookup: obs_id → `{folderPath, agentId}` |
| `mem:memories` | Global long-term memories |
| `mem:index:bm25:*` | Sharded BM25 index (2MB chunks) |
| `mem:audit` | Audit log entries |
| `mem:relations` | Knowledge graph edges |
| `mem:sessions` | Legacy session store (read-only, migration only) |
| `mem:obs:{session_id}` | Legacy per-session observations (read-only, migration only) |

### Business logic — `src/functions.py`

All canonical implementations live here. `src/memory/*` are thin shims that re-export from this module (for future decoupling).

**Observation pipeline:**
```
raw payload → normalize_folder_path() → validate_agent_id() → strip_private_data()
→ build obs dict → kv.set(folder_obs scope) → upsert folder_meta + folders index
→ kv.set(obs_lookup) → BM25-indexed → vector-indexed (if key set)
→ schedule_save() (debounced 5s) → audit log → WebSocket broadcast
```

**Memory versioning:** `remember()` checks Jaccard similarity against existing memories. If > 0.7 match found, new memory supersedes old (`isLatest=False` on old, `parentId` set on new).

**Search:** `folder_search()` uses `HybridSearch` (BM25 + vector, RRF k=60). Falls back to BM25-only when no embedding provider is configured. Results include both folder observations and global memories.

**`health_check()`** returns: `folderCount`, `agentCount`, `pairCount`, `observationCount`, `memoryCount`, `bm25IndexSize`, `vectorIndexSize`, `dbPath`, plus `db.stats()` fields.

### Search — `src/search.py`

- `SearchIndex`: BM25 with Porter stemmer and synonym expansion. Dirty-flag (`_dirty`) prevents unnecessary saves. Persisted in sharded 2MB KV chunks.
- `VectorIndex`: cosine similarity over embeddings stored as base64-encoded float32 arrays. Also has `_dirty` flag.
- `HybridSearch`: fuses BM25 + vector scores with RRF (k=60).

**Embedding providers** (auto-selected by priority in `create_app()`):

| Priority | Provider | Env var | Dimensions |
|----------|----------|---------|------------|
| 1 | `GeminiEmbeddingProvider` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | 768 |
| 2 | `OpenAIEmbeddingProvider` | `OPENAI_API_KEY` | 1536 |
| 3 | `SentenceTransformerProvider` | `AGENTCACHE_LOCAL_EMBEDDING_MODEL` | variable |
| 4 | BM25-only | — | — |

### Server — `src/app.py`

Boot order:
1. Load `~/.agentcache/.env` if present
2. Initialize `StateKV` (SQLite)
3. Auto-select embedding provider (Gemini → OpenAI → SentenceTransformer → BM25-only)
4. Initialize `IndexPersistence` (load or rebuild)
5. Backfill `obs_lookup` index if missing
6. Create Flask app, register blueprints
7. Set up WebSocket `/stream/mem-live/viewer`
8. Register CORS `after_request` hook
9. Start background workers (index rebuild if empty/stale, auto-forget loop)

Auth: all endpoints check `AGENTCACHE_SECRET` via `hmac.compare_digest`. `/livez` is always open.

## MCP Tools

The server exposes **12 MCP tools** via `GET /agentcache/mcp/tools` and `POST /agentcache/mcp/tools`.

| Tool | Description | Status |
|------|-------------|--------|
| `agent_observe` | Log observation to a `(folderPath, agentId)` pair | Working |
| `agent_remember` | Save to global long-term memory | Working |
| `memory_recall` | Search folder obs + global memories (BM25+vector) | Working |
| `memory_smart_search` | Hybrid semantic+keyword search (alias for recall) | Working |
| `memory_save` | Explicitly save insight to long-term memory | Working |
| `memory_export` | Export all data as JSON (v2 format) | Working |
| `memory_forget` | Delete memory or folder pair observations | Working |
| `memory_diagnose` | Health check (folder/agent/obs/memory counts) | Working |
| `memory_folders` | List all `(folder, agent)` pairs | Working |
| `memory_folder_observations` | Get observations for a specific pair | Working |
| `memory_timeline` | Folder activity feed (sorted by time, filterable) | Working |

**MCP stdio wrapper:** `src/mcp_stdio.py` reads `AGENTCACHE_URL` and `AGENTCACHE_SECRET` from environment variables dynamically.

## Consistency rules

**When adding a REST endpoint:**
1. Add the route in the appropriate `src/routes/*.py` blueprint
2. Update the `API Reference` section in `README.md`
3. Add the MCP tool in `src/routes/mcp.py` if it should be agent-callable

**When adding an MCP tool:**
1. Add the schema to the `GET /mcp/tools` response in `src/routes/mcp.py`
2. Add the handler case to the `POST /mcp/tools` dispatch in `src/routes/mcp.py`
3. Update the tool table in `README.md`
4. Update `AGENTS.md` tool list

**When changing data scopes:**
1. Update the `KV` class in `src/functions.py` (canonical)
2. Mirror the change in `src/storage/scopes.py`
3. Update the scope table in this file

## Code patterns

### Adding a new KV scope

```python
# In src/functions.py KV class (canonical):
class KV:
    your_scope = "mem:your-scope"

    @staticmethod
    def your_dynamic_scope(id: str) -> str:
        return f"mem:your-scope:{id}"
```

### Adding a REST endpoint

```python
# In the appropriate src/routes/*.py blueprint:
@your_bp.route('/agentcache/your-path', methods=['POST'])
def your_endpoint():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    body = request.get_json(force=True) or {}
    # validate fields explicitly — never pass raw body to functions
    result = functions.your_function(_get_kv(), body.get('field'))
    return jsonify(result), 200
```

### Adding an MCP tool schema

In `src/routes/mcp.py`, `GET /agentcache/mcp/tools` handler:
```python
{
    "name": "memory_your_tool",
    "description": "What it does",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "..."}
        },
        "required": ["query"]
    }
}
```

In `src/routes/mcp.py`, `POST /agentcache/mcp/tools` handler:
```python
elif tool_name == 'memory_your_tool':
    query = args.get('query', '')
    result = functions.your_function(kv, query)
    return jsonify({'content': [{'type': 'text', 'text': json.dumps(result)}]})
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `III_REST_PORT` / `PORT` | `3111` | Server port |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Enables Gemini vector search (priority 1) |
| `OPENAI_API_KEY` | — | Enables OpenAI vector search (priority 2) |
| `AGENTCACHE_LOCAL_EMBEDDING_MODEL` | — | SentenceTransformer model name (priority 3) |
| `AGENTCACHE_SECRET` | — | Bearer token auth |
| `AGENT_ID` | — | Default agent ID |
| `AGENTCACHE_AGENT_SCOPE=isolated` | — | Filter data to current agent |
| `AGENTCACHE_CWD` | — | Fallback folder path for legacy clients |
| `MAX_OBS_PER_FOLDER` | `2000` | Observations hard cap per (folder, agent) pair |
| `TOKEN_BUDGET` | `2000` | Context compilation cap |
| `GRAPH_EXTRACTION_ENABLED` | `false` | Graph extraction (needs LLM) |
| `CONSOLIDATION_ENABLED` | `false` | Consolidation (needs LLM) |
| `AGENTCACHE_AUTO_COMPRESS` | `false` | LLM compression |
| `AUTO_FORGET_ENABLED` | — | Auto-forget sweep (set to "false" to disable) |
| `AGENTCACHE_CORS_ORIGINS` | see app.py | Comma-separated allowed origins |
| `AGENTCACHE_IMAGE_STORE_MAX_BYTES` | 500MB | Image store byte limit |
| `HF_TOKEN` | — | HuggingFace sync |
| `AGENTCACHE_DATASET_REPO` | — | HF dataset repo for backup |

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Tests live in `tests/` — 17 test files covering unit tests, integration tests (Flask test client), and Hypothesis property tests.

Key test files:
- `tests/test_properties.py` — 8 correctness properties (pair isolation, obs count consistency, index coverage, privacy, timeline ordering, forget completeness, memory version uniqueness, path normalization idempotency)
- `tests/test_api.py` — Flask test client integration tests
- `tests/test_route_regressions.py` — regression suite after blueprint split

## HuggingFace Space deployment

Data flow: `agentcache.db` (SQLite) ↔ `sync.py` ↔ HF dataset repo.

`start.sh` sequence:
1. Restore `agentcache.db` from dataset repo
2. Start `python src/app.py` in background
3. Run `sync.py` in a loop (backup every ~60s if changed)

## Viewer — `src/viewer/index.html`

Single-file HTML dashboard, served directly by Flask at `/viewer`. No build step, no bundler.

Tabs: **Folders**, **Memories**, **Graph**, **Timeline**.

- **Folders tab**: lists all `(folder, agent)` pairs; click a row to drill into observations
- **Memories tab**: global long-term memories with search
- **Graph tab**: force-directed graph — nodes = folder paths, edges = same-parent / cross-ref / agent-shared
- **Timeline tab**: all observations sorted by timestamp desc, filterable by folder path and agent ID
