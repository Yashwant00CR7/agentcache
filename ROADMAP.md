# Roadmap

This is agentmemory-python's public roadmap. Items shift as priorities change.

- **Shipped** — in main
- **Active** — has an open PR
- **Planned** — accepted, not started
- **Candidate** — under consideration

---

## Phase 1 — Foundation (done)

- [x] Python + Flask server replacing Node.js / iii-engine
- [x] SQLite WAL backend replacing Dolt SQL
- [x] Audit log replacing Dolt git versioning
- [x] BM25 search with Porter stemmer
- [x] Gemini 768-dim vector search + hybrid RRF fusion
- [x] 16-tool MCP endpoint
- [x] WebSocket live stream
- [x] Built-in HTML viewer (Dashboard, Sessions, Memories, Graph, Timeline, Lessons, Slots, Replay)
- [x] Knowledge graph visualization — folder nodes with unique colors, force-directed layout
- [x] 4-tier memory consolidation (Working → Episodic → Semantic → Procedural)
- [x] Lessons system with confidence decay
- [x] HuggingFace Space deployment with sync fingerprinting
- [x] Dolt → SQLite one-time migration (7502 rows, 19.6MB)

---

## Phase 2 — Reliability

### Active

- [ ] **Pytest test coverage** for `src/functions.py` core operations (observe, remember, search, context)
- [ ] **Graph edge label bug** — labels overlap at default zoom on dense graphs

### Planned

- [ ] **Additional embedding providers** — OpenAI `text-embedding-3-small`, local `sentence-transformers`
- [ ] **Hook scripts** — prebuilt bash/PowerShell hook scripts for Claude Code, Cursor, Codex CLI pointing at `http://localhost:3111`
- [ ] **Memory export/import** — JSON round-trip so users can migrate between instances
- [ ] **Health endpoint** (`/agentmemory/health`) — richer than `/livez`, includes index sizes, sync status, last backup time
- [ ] **Graceful shutdown** — flush BM25/vector index to DB before exit on SIGTERM

---

## Phase 3 — Breadth

### Candidate

- **Additional LLM providers** — OpenRouter, Ollama (local), Cohere for compression and consolidation
- **MCP tool parity** — expand from 16 to 30+ tools to match the most-used subset of the Node.js 53-tool surface
- **GitHub Actions hook** — observe CI runs as memory events
- **Slack / Discord connector** — ingest messages as observations
- **Multi-agent shared memory** — namespace isolation for team use; agents share a pool with per-agent write attribution
- **RBAC** — role-based access control for shared deployments
- **Benchmark harness** — reproduce the LongMemEval-S R@5 metric on the Python stack
- **pip package** — `pip install agentmemory` with a `agentmemory` CLI entrypoint

---

## Non-goals

- Full parity with the 128-endpoint / 53-tool Node.js surface — the Python version targets simplicity and HF deployment, not feature count
- iii-engine worker model — the Python version intentionally avoids the iii runtime
- Dolt git-versioned history — the audit log satisfies the write-tracking requirement without the complexity
