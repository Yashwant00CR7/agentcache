---
title: AgentMemory Python
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

<h1 align="center">agentmemory-python</h1>

<p align="center">
  <strong>Persistent memory for AI coding agents — pure Python, zero external databases.</strong><br/>
  Works with Claude Code, Cursor, Cline, Windsurf, Gemini CLI, and any MCP client.
</p>

<p align="center">
  <a href="README.md">English</a> |
  <a href="READMEs/README.zh-CN.md">简体中文</a> |
  <a href="READMEs/README.ja-JP.md">日本語</a> |
  <a href="READMEs/README.ko-KR.md">한국어</a> |
  <a href="READMEs/README.es-ES.md">Español</a> |
  <a href="READMEs/README.hi-IN.md">हिन्दी</a> |
  <a href="READMEs/README.pt-BR.md">Português</a> |
  <a href="READMEs/README.fr-FR.md">Français</a> |
  <a href="READMEs/README.de-DE.md">Deutsch</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/SQLite-WAL-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite WAL" />
  <img src="https://img.shields.io/badge/Flask-3.0-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask 3.0" />
  <img src="https://img.shields.io/badge/MCP-Compatible-6B21A8?style=for-the-badge" alt="MCP Compatible" />
  <img src="https://img.shields.io/badge/HuggingFace-Space-FF9D00?style=for-the-badge&logo=huggingface&logoColor=white" alt="HuggingFace Space" />
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#mcp-integration">MCP</a> &bull;
  <a href="#api-reference">API</a> &bull;
  <a href="#configuration">Config</a> &bull;
  <a href="#deploy-to-huggingface">Deploy</a> &bull;
  <a href="#viewer">Viewer</a> &bull;
  <a href="#architecture">Architecture</a>
</p>

---

## What Is This?

**agentmemory-python** is a Python reimplementation of the [agentmemory](https://github.com/rohitg00/agentmemory) persistent memory server. It exposes a REST API, WebSocket stream, and MCP tools endpoint that AI coding agents use to store and retrieve session observations, long-term memories, lessons, and pinned memory slots.

Key differences from the Node.js original:

- **No Node.js or iii-engine** — runs with plain `python src/app.py`
- **SQLite instead of Dolt** — single file, WAL mode, instant startup
- **HuggingFace Space ready** — deploys in one click, data synced to an HF dataset repo
- **Same REST + MCP wire format** — drop-in for any agent already wired to agentmemory

Your agent captures every tool call, stores them as observations, compresses them into searchable memory, and injects the right context at the start of every new session — automatically.

---

## Quick Start

### Run locally

```bash
# Clone
git clone https://github.com/Yash030/agentmemory-python.git
cd agentmemory-python

# Install dependencies (no build step)
pip install -r requirements.txt

# Start the server
python src/app.py
```

Server starts on **http://localhost:3111**. Open the viewer at http://localhost:3111/viewer.

### Verify it works

```bash
# Health check
curl http://localhost:3111/agentmemory/livez
# {"status": "ok"}

# Save a memory
curl -X POST http://localhost:3111/agentmemory/remember \
  -H "Content-Type: application/json" \
  -d '{"content": "JWT auth uses jose middleware in src/middleware/auth.ts", "concepts": ["auth", "jwt"]}'

# Recall it
curl -X POST http://localhost:3111/agentmemory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication middleware", "limit": 5}'
```

---

## Features

| Feature | Status | Notes |
|---------|--------|-------|
| REST API — sessions, memories, observations | ✅ | Full surface |
| WebSocket live stream | ✅ | `/stream/mem-live/viewer` |
| MCP tools endpoint | ✅ | 31 tools |
| Built-in HTML viewer | ✅ | Real-time dashboard at `/viewer` |
| BM25 keyword search | ✅ | Always on, no API key needed |
| Hybrid BM25 + vector search | ✅ | Requires `GEMINI_API_KEY` |
| 4-tier memory consolidation | ⚙️ | `CONSOLIDATION_ENABLED=true` + LLM key |
| Knowledge graph extraction | ⚙️ | `GRAPH_EXTRACTION_ENABLED=true` + LLM key |
| LLM observation compression | ⚙️ | `AGENTMEMORY_AUTO_COMPRESS=true` + LLM key |
| Lessons with confidence decay | ✅ | Fingerprinted, auto-strengthen on repeat |
| Memory slots (pinned context) | ✅ | CRUD + auto-reflect |
| Session replay | ✅ | Full timeline in viewer |
| Audit log | ✅ | Tracks every write with agent_id + timestamp |
| HuggingFace Space deploy | ✅ | One-click, data synced to dataset repo |
| Privacy filtering | ✅ | Strips API keys, tokens before storage |

### 4-Tier Memory Model

Inspired by how human memory works — raw experience → compressed episodes → extracted facts → learned patterns.

| Tier | What | When |
|------|------|------|
| **Working** | Raw observations from tool use | Every tool call |
| **Episodic** | Compressed session summaries | Session end |
| **Semantic** | Extracted facts and patterns | Consolidation |
| **Procedural** | Workflows and decision patterns | Consolidation |

---

## MCP Integration

Wire agentmemory-python into your agent's MCP config. It speaks the same MCP protocol as the Node.js original.

### Most agents (Cursor, Claude Desktop, Cline, Windsurf)

```json
{
  "mcpServers": {
    "agentmemory": {
      "command": "npx",
      "args": ["-y", "@agentmemory/mcp"],
      "env": {
        "AGENTMEMORY_URL": "http://localhost:3111"
      }
    }
  }
}
```

### Claude Code

Paste this prompt and your agent will wire everything:

```
Start agentmemory-python: run `python src/app.py` from the agentmemory-python directory.
Then add this MCP server to ~/.claude.json under mcpServers:
{
  "agentmemory": {
    "command": "npx",
    "args": ["-y", "@agentmemory/mcp"],
    "env": { "AGENTMEMORY_URL": "http://localhost:3111" }
  }
}
Verify with: curl http://localhost:3111/agentmemory/livez
Open the viewer at: http://localhost:3111/viewer
```

### Available MCP Tools (31)

| Tool | Description |
|------|-------------|
| `memory_save` | Save a long-term insight, decision, or pattern |
| `memory_recall` | Search past observations by keyword |
| `memory_smart_search` | Hybrid BM25 + vector semantic search |
| `memory_sessions` | List recent sessions |
| `memory_sessions_list` | Retrieve all memory sessions |
| `memory_timeline` | Chronological observations for a session |
| `memory_observations` | Observations for a session |
| `memory_profile` | Per-project concept + file profile |
| `memory_lessons` | List active lessons with confidence scores |
| `memory_lesson_save` | Save a lesson (duplicate saves strengthen it) |
| `memory_lesson_recall` | Search lessons by query |
| `memory_lesson_search` | Search lessons by keywords |
| `memory_consolidate` | Run 4-tier memory consolidation |
| `memory_reflect` | Reflect on session, update context |
| `memory_diagnose` | Health check across all subsystems |
| `memory_forget` | Delete memory, session, or observations |
| `memory_export` | Export all memory data as JSON |
| `agent_observe` | Log agent execution observation |
| `agent_remember` | Save agent memory to long-term storage |
| `memory_antigravity_sync` | Sync Antigravity transcripts to memory |
| `memory_antigravity_sync_all` | Master sync: transcript + crystallize + reflect |
| `memory_slot_list` | List all pinned memory slots |
| `memory_slot_get` | Retrieve a specific pinned memory slot |
| `memory_slot_create` | Create/overwrite a pinned memory slot |
| `memory_slot_append` | Append text content to a pinned memory slot |
| `memory_slot_replace` | Replace pinned memory slot content |
| `memory_slot_delete` | Delete a pinned memory slot |
| `memory_action_create` | Create a new work item / action |
| `memory_action_update` | Update fields of an existing action |
| `memory_frontier` | Get active and pending actions sorted by priority |
| `memory_crystallize` | Crystallize/summarize observations in a session |

---

## API Reference

Base URL: `http://localhost:3111/agentmemory`

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/livez` | Liveness probe — no auth required |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/session/start` | Start a new session |
| `POST` | `/session/end` | End a session |
| `POST` | `/session/commit` | Commit session with summary |
| `GET` | `/sessions` | List all sessions |

### Observations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/observe` | Ingest a hook event observation |
| `POST` | `/agent/observe` | Simplified observe for direct agent use |
| `GET` | `/observations` | List observations (`?session_id=`) |
| `POST` | `/timeline` | Chronological observation window |

### Memories

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/remember` | Save long-term memory |
| `POST` | `/agent/remember` | Simplified remember |
| `POST` | `/forget` | Delete memory / session / observations |
| `POST` | `/search` | BM25 + vector search |
| `POST` | `/context` | Compile context for a session + project |
| `GET` | `/memories` | List memories (`?latest=true&limit=N`) |
| `POST` | `/evolve` | Create a new memory version |

### Lessons

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/lessons` | List lessons |
| `POST` | `/lessons` | Create lesson |
| `POST` | `/lessons/search` | Search lessons |
| `POST` | `/lessons/strengthen` | Reinforce an existing lesson |

### Slots

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/slots` | List all pinned slots |
| `POST` | `/slot` | Create or update a slot |
| `GET` | `/slot` | Get slot by name |
| `DELETE` | `/slot` | Delete a slot |
| `POST` | `/slot/reflect` | Auto-populate from session observations |

### Graph + Profile

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/relations` | Knowledge graph edges |
| `POST` | `/relations` | Add a relation |
| `GET` | `/profile` | Project profile (top concepts, files) |

### Actions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/actions` | List actions |
| `POST` | `/actions` | Create an action |
| `PATCH` | `/actions/<id>` | Update action status / fields |
| `GET` | `/frontier` | Pending actions sorted by priority |
| `GET` | `/insights` | List insights |

### Replay

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/replay/sessions` | Sessions list for replay tab |
| `GET` | `/replay/load` | Full session + observations (`?sessionId=`) |

### MCP

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/mcp/tools` | MCP tool schema list |
| `POST` | `/mcp/tools` | MCP tool call dispatch |

---

## Configuration

Create `~/.agentmemory/.env` (no `export` prefix needed):

```env
# Server port
III_REST_PORT=3111

# Vector search — enables Gemini 768-dim embeddings
GEMINI_API_KEY=your-gemini-key

# LLM for compression / consolidation / graph extraction
# Any one of these enables LLM features:
ANTHROPIC_API_KEY=your-anthropic-key
# OPENAI_API_KEY=your-openai-key
# GEMINI_API_KEY=your-key   (same key as above works for both)

# LLM-powered features (disabled by default — spend tokens)
CONSOLIDATION_ENABLED=true
GRAPH_EXTRACTION_ENABLED=true
AGENTMEMORY_AUTO_COMPRESS=true

# Context injection limits
TOKEN_BUDGET=2000
MAX_OBS_PER_SESSION=500

# Auth — set to require Bearer token on all endpoints
AGENTMEMORY_SECRET=your-secret

# Agent scope isolation
AGENT_ID=my-agent
AGENTMEMORY_AGENT_SCOPE=isolated   # only see this agent's data

# HuggingFace sync
HF_TOKEN=your-hf-token
AGENTMEMORY_DATASET_REPO=username/agentmemory-data
```

### Full Variable Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `III_REST_PORT` / `PORT` | `3111` | API server port |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Enables 768-dim vector search |
| `AGENTMEMORY_SECRET` | — | Bearer token auth on all endpoints |
| `AGENT_ID` | — | Default agent ID for scope isolation |
| `AGENTMEMORY_AGENT_SCOPE=isolated` | — | Filters data to current `AGENT_ID` |
| `MAX_OBS_PER_SESSION` | `500` | Hard cap on observations per session |
| `TOKEN_BUDGET` | `2000` | Max tokens in compiled context |
| `GRAPH_EXTRACTION_ENABLED` | `false` | Knowledge graph (needs LLM) |
| `CONSOLIDATION_ENABLED` | `false` | Memory consolidation (needs LLM) |
| `AGENTMEMORY_AUTO_COMPRESS` | `false` | LLM observation compression |

---

## Viewer

Built-in dashboard at **http://localhost:3111/viewer**.

| Tab | What You See |
|-----|-------------|
| **Dashboard** | Session stats, memory counts, recent activity |
| **Sessions** | Browse sessions, inspect observations |
| **Memories** | Search, filter, and read long-term memories |
| **Graph** | Project folder visualization — nodes = folders, edges = shared concepts or parent path |
| **Timeline** | Per-session chronological observation view |
| **Lessons** | Confidence-scored lessons with decay tracking |
| **Slots** | Pinned memory slots editor |
| **Replay** | Scrub through past sessions frame by frame |

---

## Deploy to HuggingFace

This project is designed to run as a HuggingFace Space. Data is stored in an HF dataset repo and restored on every boot — so no persistent disk is needed.

### Setup

1. Fork this repo as a HuggingFace Space (SDK: Docker)
2. Create a dataset repo (e.g. `your-username/agentmemory-data`)
3. Add Space secrets in the HF dashboard:

   | Secret | Value |
   |--------|-------|
   | `HF_TOKEN` | Your HF write token |
   | `AGENTMEMORY_DATASET_REPO` | `your-username/agentmemory-data` |
   | `AGENTMEMORY_SECRET` | A random secret (optional but recommended) |
   | `GEMINI_API_KEY` | Gemini key (optional, enables vector search) |

4. The Space boots, restores `agentmemory.db` from the dataset repo, and starts the server

### How sync works

`sync.py` uses mtime fingerprinting — it only uploads when the database actually changed, so there are no unnecessary uploads during idle periods.

```bash
# Manual backup
python sync.py

# Environment for sync
HF_TOKEN=...
AGENTMEMORY_DATASET_REPO=username/agentmemory-data
```

---

## Architecture

```
agentmemory-python/
├── src/
│   ├── app.py          Flask server — all endpoints, WebSocket broadcaster
│   ├── db.py           SQLite StateKV — WAL mode, audit_log table
│   ├── functions.py    Core logic — observe, remember, search, context
│   ├── search.py       BM25 + Gemini vector index + HybridSearch (RRF)
│   └── viewer/
│       └── index.html  Single-file HTML dashboard (no build step)
├── sync.py             HuggingFace dataset backup/restore
├── Dockerfile          HF Space container
├── start.sh            Boot script (restore → start server → start sync)
└── requirements.txt    6 Python dependencies, no external DB required
```

### Database layout

Two SQLite tables in `~/.agentmemory/agentmemory.db`:

```sql
-- All data lives here, namespaced by scope
kv_store (
  scope TEXT NOT NULL,    -- e.g. "mem:sessions", "mem:obs:{session_id}"
  key   TEXT NOT NULL,
  value TEXT NOT NULL,    -- JSON-serialized
  PRIMARY KEY (scope, key)
)

-- Audit trail replaces Dolt git versioning
audit_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       INTEGER NOT NULL,   -- unix millis
  agent_id TEXT NOT NULL,
  message  TEXT NOT NULL
)
```

### Search pipeline

```
Query
  → BM25 (always)         — Porter-stemmed keyword matching
  → Vector (if Gemini key) — 768-dim cosine similarity
  → RRF fusion            — Reciprocal Rank Fusion (k=60)
  → Session diversify     — max 3 results per session
  → Return top-K
```

---

## vs Original agentmemory

| | agentmemory (Node.js) | agentmemory-python |
|---|---|---|
| Runtime | Node.js 20+ | Python 3.10+ |
| Storage | Dolt SQL (git-versioned MySQL) | SQLite WAL (single file) |
| Engine dependency | iii-engine (separate binary) | None — just Flask |
| Embeddings | 6 providers + local `@xenova/transformers` | Gemini 768-dim |
| MCP tools | 53 | 31 |
| REST endpoints | 128 | ~50 |
| Deploy | npm, Docker, fly.io, Railway, Render | Docker, HuggingFace Spaces |
| Cold boot | ~7s (iii engine warm-up) | <2s |
| Database size | ~232MB (417 Dolt chunk files) | ~20MB (single `.db` file) |
| Setup | `npm install -g @agentmemory/agentmemory` | `pip install -r requirements.txt` |

Choose the Python version for: simpler setup, HF Space deployment, single-file database, no Node.js, or Python ecosystem integration.

Choose the Node.js version for: the full 53-tool MCP surface, iii-engine observability, production multi-agent deployments, or the full auto-hook suite.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

Priority areas: test coverage, additional embedding providers, more agent hook scripts.

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
