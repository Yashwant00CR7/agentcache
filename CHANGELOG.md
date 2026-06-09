# Changelog

All notable changes to agentmemory-python are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions track the Python reimplementation, not the upstream Node.js agentmemory releases.

---

## [Unreleased]

---

## [1.0.0] — 2026-06-09

Initial public release of the Python reimplementation.

### Added

- **Flask REST server** (`src/app.py`) — full API surface matching the agentmemory wire format: sessions, observations, memories, lessons, slots, relations, actions, replay, context, search, MCP
- **SQLite storage** (`src/db.py`) — `StateKV` backed by a single WAL-mode SQLite file at `~/.agentmemory/agentmemory.db`; replaces Dolt SQL Server
- **Audit log** — `audit_log(id, ts, agent_id, message)` table replaces Dolt git versioning; every write is tracked
- **BM25 search** (`src/search.py`) — custom Porter stemmer, in-memory index, persisted to DB in 2MB shards
- **Vector search** (`src/search.py`) — Gemini `text-embedding-004` 768-dim cosine similarity; enabled by `GEMINI_API_KEY`
- **Hybrid search** — RRF fusion of BM25 + vector (k=60), session-diversified (max 3 per session)
- **MCP tools endpoint** — 16 tools via `GET/POST /agentmemory/mcp/tools`
- **WebSocket live stream** — `/stream/mem-live/viewer` broadcasts all observations in real time
- **Built-in viewer** (`src/viewer/index.html`) — single-file HTML dashboard: Dashboard, Sessions, Memories, Graph, Timeline, Lessons, Slots, Replay tabs
- **Knowledge graph visualization** — Graph tab shows project folders as nodes with unique hash-based HSL colors; edges represent shared concepts or common parent paths
- **4-tier memory consolidation** — Working → Episodic → Semantic → Procedural; enabled by `CONSOLIDATION_ENABLED=true` + LLM key
- **Lessons system** — confidence-scored, fingerprinted by SHA-256, duplicate saves strengthen confidence, weekly decay, auto-evict below 0.1
- **Memory versioning** — Jaccard similarity check; memories with > 0.7 overlap supersede each other with `parentId` linkage
- **Session lifecycle** — start, end, commit with summary; auto-close dangling active sessions on new session start
- **HuggingFace Space support** — `Dockerfile`, `start.sh`, `sync.py` for deploy-and-forget on HF Spaces
- **Sync with fingerprinting** (`sync.py`) — mtime+size hash prevents redundant uploads; only backs up when DB actually changed
- **Privacy filtering** — API keys, secrets, `<private>` tags stripped before storage
- **Bearer token auth** — timing-safe `hmac.compare_digest` check on all endpoints when `AGENTMEMORY_SECRET` is set
- **Agent scope isolation** — `AGENTMEMORY_AGENT_SCOPE=isolated` filters all reads to the current `AGENT_ID`

### Migrated

- `migrate_dolt_to_sqlite.py` — one-time migration script that reads all rows from a local Dolt SQL server and writes them to SQLite; migrated 7502 rows (19.6MB) from the original deployment

### Removed

- Dolt SQL server dependency — no more `dolt sql-server`, no more `pymysql`, no more Dolt install in Dockerfile
- Node.js / iii-engine dependency — the entire iii-engine binary and worker model is gone
- `start.sh` Dolt startup block — `sleep 5`, `DOLT_*` env vars, migration step all removed

### Dependencies (6 total)

```
flask>=3.0.0
flask-sock>=0.7.0
requests>=2.31.0
websockets>=12.0
python-dateutil>=2.8.2
huggingface_hub>=0.20.0
```

---

## Format

- **Added** — new features
- **Changed** — changes to existing behavior
- **Fixed** — bug fixes
- **Removed** — removed features
- **Security** — security fixes
- **Migrated** — data or infrastructure migrations
