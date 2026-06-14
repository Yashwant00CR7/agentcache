# agentmemory-python — Architecture

## Overview

A Python REST + WebSocket + MCP memory server backed by SQLite.
Agents store observations scoped by `(folderPath, agentId)` pairs and retrieve
context at session start. No Node.js, no external database, no build step.

---

## Module Responsibilities

```
src/
├── app.py              Flask application factory (create_app).
│                       Initialises DB, embeddings, blueprints, WebSocket,
│                       CORS hook, and background workers.
│
├── routes/             Flask blueprints — one per domain area.
│   ├── __init__.py     register_blueprints(app) helper.
│   ├── observations.py /observe, /agent/observe, /folders, /folder/observations
│   ├── memories.py     /remember, /agent/remember, /memories, /forget
│   ├── search.py       /search, /timeline
│   ├── graph.py        /graph, /graph/stats, /graph/query, /graph/build
│   ├── health.py       /livez, /health, /audit, /config/flags
│   ├── mcp.py          GET+POST /mcp/tools
│   └── migration.py    /migrate
│
├── functions.py        Core business logic.
│                       observe(), folder_observe(), remember(), forget(),
│                       folder_search(), folder_timeline(), health_check(),
│                       export_data(), rebuild_index(), auto_forget(),
│                       folder_graph_build(), KV scope registry.
│
├── db.py               StateKV — SQLite wrapper (WAL mode, kv_store + audit_log).
│
├── search.py           SearchIndex (BM25 + Porter stemmer + synonyms),
│                       VectorIndex (cosine similarity, base64 float32),
│                       GeminiEmbeddingProvider, HybridSearch (RRF).
│
├── workers.py          Daemon threads: index rebuild, auto-forget sweep,
│                       graceful shutdown (SIGTERM/SIGINT).
│
├── viewer_helpers.py   make_viewer_response() — reads viewer/index.html,
│                       injects nonce + version, sets CSP headers.
│
├── mcp_stdio.py        stdio MCP bridge: reads AGENTMEMORY_URL and
│                       AGENTMEMORY_SECRET, proxies tool calls to the HTTP API.
│
└── viewer/
    └── index.html      Single-file HTML dashboard (no bundler).
```

---

## KV Scope Layout

All data lives in a single SQLite file (`~/.agentmemory/agentmemory.db`) in two tables:

- `kv_store(scope TEXT, key TEXT, value TEXT, PRIMARY KEY(scope, key))` — JSON values
- `audit_log(id, ts, agent_id, message)` — write audit trail

| Scope | Content |
|-------|---------|
| `mem:folders` | Global index of all `(folderPath, agentId)` pairs |
| `mem:folder:{path}:{agent}` | Observations for one `(folder, agent)` pair |
| `mem:foldermeta:{path}:{agent}` | Metadata for one pair (obsCount, lastUpdated, summary) |
| `mem:memories` | Long-term global memories |
| `mem:index:bm25` | BM25 index shards (manifest + data chunks) |
| `mem:audit` | Audit log entries (via `record_audit()`) |
| `mem:relations` | Knowledge graph edges |
| `mem:sessions` | Legacy session objects (read-only for migration) |
| `mem:obs:{session_id}` | Legacy session observations (read-only for migration) |

---

## Data Flow

### Observation Ingestion

```
POST /agent/observe
  └─► folder_observe(kv, payload)
        1. Validate folderPath, agentId, text, timestamp
        2. normalize_folder_path() + validate_agent_id()
        3. strip_private_data()
        4. Cap text at 4000 chars
        5. Enforce MAX_OBS_PER_FOLDER cap
        6. Generate obs_id (fobs_...)
        7. Write FolderObservation to KV.folder_obs(fp, aid)
        8. Upsert folder metadata (KV.folder_meta)
        9. Upsert global folders index (KV.folders)
       10. Add to BM25 index (_bm25_index.add)
       11. Add to vector index if embedding provider is set
       12. Debounce persistence save (IndexPersistence.schedule_save)
       13. Write audit log entry (kv.commit_version)
       14. Broadcast via WebSocket (/stream/mem-live/viewer)
        └─► return {"observationId": obs_id}
```

### Search

```
POST /search  or  POST /mcp/tools {name:"memory_recall"}
  └─► folder_search(kv, query, limit, folderPath?, agentId?)
        1. HybridSearch.search() → BM25 + vector RRF fusion
        2. Load all (folder, agent) pairs from KV.folders
        3. Hydrate obs_ids from KV.folder_obs scopes
        4. Apply folderPath/agentId post-filters
        5. Also include matching global memories
        6. Sort by score descending, cap at limit
```

### Memory Versioning

`remember()` scans existing memories for Jaccard similarity > 0.7.
If found, old memory is marked `isLatest=False` and new memory sets `parentId`.

---

## Authentication

All endpoints except `/livez` check `AGENTMEMORY_SECRET` via timing-safe
`hmac.compare_digest` Bearer token comparison. No secret → no auth check.

---

## WebSocket

`/stream/mem-live/viewer` broadcasts raw JSON payloads to connected viewers.
The viewer's "Replay" tab subscribes to this stream for live observation updates.

---

## Embedding Providers

Priority order (auto-selected at startup):

1. `GeminiEmbeddingProvider` — if `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set (768 dims)
2. `OpenAIEmbeddingProvider` — if `OPENAI_API_KEY` is set (1536 dims)
3. `SentenceTransformerProvider` — if `AGENTMEMORY_LOCAL_EMBEDDING_MODEL` is set
4. BM25-only fallback

Without an embedding provider, `HybridSearch` falls back to pure BM25.
