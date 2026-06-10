# agentmemory-python — Agent Instructions

## What this project is

A Python REST + WebSocket + MCP memory server backed by SQLite. No Node.js, no iii-engine, no Dolt. Agents use it to store observations, memories, lessons, and slots, and to retrieve context at session start.

## Project layout

```
src/
├── app.py          Flask server — all endpoints, WebSocket broadcaster
├── db.py           SQLite StateKV — WAL mode, audit_log table
├── functions.py    Core business logic (observe, remember, search, context)
├── search.py       BM25 index + Gemini vector index + HybridSearch (RRF)
└── viewer/
    └── index.html  Single-file HTML dashboard
sync.py             HuggingFace dataset backup/restore
Dockerfile          HF Space container definition
start.sh            Boot: restore DB → start server → start sync loop
requirements.txt    flask, flask-sock, requests, websockets, python-dateutil, huggingface_hub
```

## Running

```bash
pip install -r requirements.txt
python src/app.py
# Server on http://localhost:3111
# Viewer at http://localhost:3111/viewer
```

No build step. No external database. SQLite file lives at `~/.agentmemory/agentmemory.db`.

## Architecture

### Storage — `src/db.py`

`StateKV` wraps a single SQLite file with two tables:

- `kv_store(scope TEXT, key TEXT, value TEXT, PRIMARY KEY(scope, key))` — all data as JSON, namespaced by scope
- `audit_log(id, ts, agent_id, message)` — write audit trail (replaces Dolt versioning)

Key scopes (defined in `functions.py` `KV` class):

| Scope | Content |
|-------|---------|
| `mem:sessions` | Session objects |
| `mem:obs:{session_id}` | Observations for a session |
| `mem:memories` | Long-term memories |
| `mem:lessons` | Lessons with confidence scores |
| `mem:slots` | Pinned memory slots |
| `mem:relations` | Knowledge graph edges |
| `mem:actions` | Work items / actions |

### Business logic — `src/functions.py`

Global state:
- `_bm25_index` / `_vector_index` — in-memory search indexes, rebuilt from DB on startup if empty
- `_hybrid_search` — combines BM25 + vector; only initialized when Gemini key is set
- `_stream_broadcaster` — WebSocket broadcast callback injected by `app.py`

**Observation pipeline:**
```
raw payload → strip_private_data() → build_synthetic_compression()
→ stored in kv_store → BM25-indexed → vector-indexed (if key set)
→ audit_log entry → WebSocket broadcast
```

**Memory versioning:** `remember()` checks Jaccard similarity against existing memories. If > 0.7 match found, new memory supersedes old (`isLatest=False` on old, `parentId` set on new).

**Context compilation** (`context()`): assembles pinned slots → project profile → lessons (scored by confidence × project match) → past session summaries, capped at `TOKEN_BUDGET` tokens (estimated at `len/3`).

**Lessons:** fingerprinted by SHA-256 of content. Duplicate saves strengthen confidence (`+0.1 × (1 - conf)`). Weekly decay reduces confidence by `decayRate × weeks`; soft-deleted at ≤ 0.1 confidence with 0 reinforcements.

### Search — `src/search.py`

- `SearchIndex`: BM25 with custom Porter stemmer. Persisted to `kv_store` in sharded 2MB chunks via `IndexPersistence`.
- `VectorIndex`: cosine similarity over Gemini 768-dim embeddings stored as base64-encoded float32 arrays.
- `HybridSearch`: fuses BM25 + vector scores with RRF (k=60, reciprocal rank fusion).

### Server — `src/app.py`

Boot order:
1. Initialize `StateKV` (SQLite)
2. Initialize embedding provider (Gemini if key set)
3. Initialize `IndexPersistence`
4. Rebuild BM25/vector index if empty (background thread)
5. Start Flask on `III_REST_PORT` (default 3111)

Auth: all endpoints check `AGENTMEMORY_SECRET` via timing-safe `hmac.compare_digest` Bearer token comparison if the env var is set. `/livez` is always open.

WebSocket at `/stream/mem-live/viewer` broadcasts raw + compressed observations to connected viewers.

## MCP Tools

The server exposes 30 MCP tools via `GET /agentmemory/mcp/tools` (schema) and `POST /agentmemory/mcp/tools` (execution).

| Tool | Description | Status |
|------|-------------|--------|
| `memory_recall` | Search past session observations | Working |
| `memory_save` | Save long-term memory (concepts/files as string or array) | Working |
| `memory_sessions` | List recent sessions | Working |
| `memory_sessions_list` | Retrieve all memory sessions | Working |
| `memory_smart_search` | Hybrid semantic+keyword search | Working |
| `memory_timeline` | Chronological observations | Working |
| `memory_observations` | Get observations for session | Working |
| `memory_profile` | User/project profile | Working |
| `memory_lessons` | List saved lessons | Working |
| `memory_lesson_save` | Save lesson from session | Working |
| `memory_lesson_recall` | Search lessons by query | Working |
| `memory_lesson_search` | Search lessons (keywords) | Working |
| `memory_consolidate` | Summarize sessions, extract memory | Working |
| `memory_reflect` | Reflect on session, update context | Working |
| `memory_diagnose` | Health check subsystems | Working |
| `memory_forget` | Delete memory/session/observations | Working |
| `memory_export` | Export all data as JSON | Working |
| `agent_observe` | Log agent execution observation | Working |
| `agent_remember` | Save agent memory to long-term | Working |
| `memory_antigravity_sync` | Sync Antigravity transcripts | Working |
| `memory_slot_list` | List all pinned memory slots | Working |
| `memory_slot_get` | Retrieve a specific pinned slot | Working |
| `memory_slot_create` | Create/overwrite pinned slot | Working |
| `memory_slot_append` | Append text content to slot | Working |
| `memory_slot_replace` | Replace slot content | Working |
| `memory_slot_delete` | Delete pinned memory slot | Working |
| `memory_action_create` | Create a new work item / action | Working |
| `memory_action_update` | Update fields of existing action | Working |
| `memory_frontier` | Get active/pending actions | Working |
| `memory_crystallize` | Summarize session observations | Working |

**MCP stdio wrapper:** `src/mcp_stdio.py` reads `AGENTMEMORY_URL` and `AGENTMEMORY_SECRET` from environment variables dynamically.

## Consistency rules

**When adding a REST endpoint:**
1. Add the route in `src/app.py`
2. Update `API Reference` section in `README.md`
3. Add the MCP tool in `src/app.py` MCP dispatch if it should be agent-callable

**When adding an MCP tool:**
1. Add the schema to the `GET /mcp/tools` response in `src/app.py`
2. Add the handler case to the `POST /mcp/tools` dispatch in `src/app.py`
3. Update the tool table in `README.md`
4. Update `AGENTS.md` tool list

**When changing data scopes:**
1. Update the `KV` class in `src/functions.py`
2. Update the scope table in this file

## Code patterns

### Adding a new KV scope

```python
class KV:
    your_scope = "mem:your-scope"

    @staticmethod
    def your_dynamic_scope(id: str) -> str:
        return f"mem:your-scope:{id}"
```

### Adding a REST endpoint

```python
@app.route('/agentmemory/your-path', methods=['POST'])
def your_endpoint():
    if AGENTMEMORY_SECRET:
        auth = request.headers.get('Authorization', '')
        if not hmac.compare_digest(auth, f'Bearer {AGENTMEMORY_SECRET}'):
            return jsonify({'error': 'Unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    # validate fields explicitly — never pass raw body to functions
    result = your_function(kv, body.get('field'))
    return jsonify(result), 200
```

### Adding an MCP tool schema

In the `GET /mcp/tools` handler, add to the tools list:
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

In the `POST /mcp/tools` handler, add a case:
```python
elif tool_name == 'memory_your_tool':
    query = args.get('query', '')
    result = your_function(kv, query)
    return jsonify({'content': [{'type': 'text', 'text': json.dumps(result)}]})
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `III_REST_PORT` / `PORT` | `3111` | Server port |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Enables vector search + Gemini LLM |
| `AGENTMEMORY_SECRET` | — | Bearer token auth |
| `AGENT_ID` | — | Default agent ID |
| `AGENTMEMORY_AGENT_SCOPE=isolated` | — | Filter data to current agent |
| `MAX_OBS_PER_SESSION` | `500` | Observations hard cap |
| `TOKEN_BUDGET` | `2000` | Context compilation cap |
| `GRAPH_EXTRACTION_ENABLED` | `false` | Graph extraction (needs LLM) |
| `CONSOLIDATION_ENABLED` | `false` | Consolidation (needs LLM) |
| `AGENTMEMORY_AUTO_COMPRESS` | `false` | LLM compression |
| `HF_TOKEN` | — | HuggingFace sync |
| `AGENTMEMORY_DATASET_REPO` | — | HF dataset repo for backup |

## HuggingFace Space deployment

Data flow: `agentmemory.db` (SQLite) ↔ `sync.py` ↔ HF dataset repo.

`sync.py` uses mtime fingerprinting (`_quick_hash`) to detect changes before uploading. Backup only runs when the DB actually changed. Restore uses `hf_hub_download` for targeted file fetches rather than full `snapshot_download`.

`start.sh` sequence:
1. Restore `agentmemory.db` from dataset repo
2. Start `python src/app.py` in background
3. Run `sync.py` in a loop (backup every ~60s if changed)

## Viewer — `src/viewer/index.html`

Single-file HTML dashboard, served directly by Flask at `/viewer`. No build step, no bundler.

Tabs: Dashboard, Sessions, Memories, Graph, Timeline, Lessons, Slots, Replay.

**Graph tab** (`loadGraph()`): fetches sessions + memories, groups by `project` path into folder nodes. Edges connect folders that share concepts or parent path segments. Each folder node gets a unique color via `folderColor(id)` — a hash-to-hex function that converts the folder path string into a distinct HSL color. The simulation uses force-directed physics with per-node-count repulsion tuning.

## No tests yet

No test runner is configured. When adding tests, use `pytest` — it's the standard Python choice and requires no extra config for basic test discovery (`test_*.py` files).
