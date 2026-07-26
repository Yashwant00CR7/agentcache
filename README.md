# agentcache

A Python REST + WebSocket + MCP cache server for AI agents, backed by SQLite.

`agentcache` gives coding agents (Claude Code, Cursor, Cline, Kiro, Antigravity, …) a persistent, searchable memory of what happened in a project — observations, sessions, long-term memories, and a folder-scoped knowledge graph — with hybrid BM25 + vector search, WebSocket live updates, and a Model Context Protocol (MCP) stdio bridge.

- **Server:** Flask + `flask-sock` (REST + WebSocket) on a single port
- **Storage:** SQLite in WAL mode (zero external services)
- **Search:** BM25 + optional local embeddings (`sentence-transformers`) or Hugging Face inference
- **MCP:** stdio bridge so any MCP-compatible client can `observe` / `remember` / `search` natively
- **CLI:** `agentcache serve|worker|migrate|export|context|connect`

Status: **Beta** (`Development Status :: 4 - Beta`). API is stable enough for daily use; internals may still shift before 1.0.

---

## Install

```bash
pip install agentcache-core
```

The package installs as `agentcache-core` on PyPI but imports and runs as `agentcache` (`import agentcache`, `agentcache serve`). The plain `agentcache` name on PyPI belongs to an unrelated project.

With optional local embeddings (adds `sentence-transformers`, ~1 GB of model weights on first use):

```bash
pip install "agentcache-core[local-embeddings]"
```

Requires Python 3.10+.

---

## Quickstart

Start the server:

```bash
agentcache serve --port 3111
```

Record an observation (any HTTP client works):

```bash
curl -X POST http://localhost:3111/agentcache/observe \
  -H "Content-Type: application/json" \
  -d '{
        "folderPath": "/abs/path/to/project",
        "agentId": "coder-1",
        "text": "Refactored auth into a decorator; see routes/auth.py",
        "type": "refactor"
      }'
```

Search across a folder:

```bash
curl "http://localhost:3111/agentcache/folder/observations?folderPath=/abs/path/to/project&q=auth"
```

Health check:

```bash
curl http://localhost:3111/agentcache/health
```

Generate a live context file for your IDE:

```bash
cd /abs/path/to/project
agentcache context --watch     # writes .agentcache_context.md and re-writes on change
```

---

## Programmatic use

```python
from agentcache import create_app, StateKV, remember

app = create_app()                 # Flask app, ready for WSGI or app.run()
kv  = StateKV()                    # direct KV access to the SQLite store
remember(kv, {"title": "auth decorator", "content": "…", "agentId": "coder-1"})
```

Top-level exports: `create_app`, `StateKV`, `KV`, `ObservationStore`, `ObservationEvents`, `SearchService`, `remember`, `folder_graph_build`, `health_check`, `run_connect`.

---

## Wire it into your agent

```bash
agentcache connect claude-code    # or: cursor, cline, kiro, antigravity, codex, hermes, vscode
agentcache connect --all          # detect and wire every supported client
```

This registers the MCP stdio bridge (`agentcache.mcp_stdio`) and, with `--with-hooks`, installs workspace hook blocks that call `/agentcache/observe` on file events. Re-running is safe — the CLI detects stale entries (wrong interpreter path, missing env keys, etc.) and repairs them, or prints a `--force` hint when the entry is already up to date.

See [docs/mcp-setup.md](docs/mcp-setup.md) for per-client config paths, manual wiring, and troubleshooting.

---

## Auth

Set `AGENTCACHE_SECRET` to require a shared secret on write routes:

```bash
export AGENTCACHE_SECRET=your-long-random-string
agentcache serve
```

Clients send it via `Authorization: Bearer <secret>` or `?secret=<secret>`. Unset = open (fine for `localhost`; **do not** expose an unauthenticated instance).

---

## Deployment

`Dockerfile` and `docker-compose.yml` are included. For multi-worker WSGI (Gunicorn + workers), run the server without background threads and use a dedicated worker process:

```bash
agentcache serve --no-workers                    # web tier
agentcache worker --tasks=index,forget           # sidecar
```

Details and the ongoing hardening plan live in [`docs/publishing_roadmap.md`](docs/publishing_roadmap.md).

---

## Compatibility notes

- Routes are dual-mounted at both `/agentcache/*` and `/agentmemory/*` for backward compatibility with the previous package name.
- `agentcache.legacy` is a thin re-export shim retained for callers that imported from the pre-split module layout. It will be removed in **1.0** — migrate imports to `agentcache.core.*` when convenient. See [`docs/adr/`](docs/adr/) for the split rationale.

---

## Development

```bash
git clone https://github.com/Yashwant00CR7/agentcache
cd agentcache
pip install -e ".[dev,local-embeddings]"

pytest                     # test suite
ruff check .               # lint
ruff format .              # format
python -m build            # build sdist + wheel into dist/
twine check dist/*         # validate metadata before upload
```

---

## Links

- **Source:** https://github.com/Yashwant00CR7/agentcache
- **Issues:** https://github.com/Yashwant00CR7/agentcache/issues
- **License:** MIT — see [LICENSE](LICENSE)
