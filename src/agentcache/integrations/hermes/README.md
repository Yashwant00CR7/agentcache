# agentcache for Hermes Agent

A memory provider plugin that lets [Hermes Agent](https://github.com/NousResearch) remember work across sessions by delegating to a locally running [agentcache](https://github.com/Yashwant00CR7/agentcache) server — REST + WebSocket + MCP, backed by SQLite.

Hermes ships with `MEMORY.md`/`USER.md` and SQLite FTS. This plugin adds structured observations, folder-scoped project profiles, hybrid BM25 + vector search, and six lifecycle hooks that wire memory in *around* every turn, not just alongside it.

- **Provider name:** `agentcache`
- **Transport:** HTTP against `AGENTCACHE_URL` (default `http://localhost:3111`)
- **Hooks:** `prefetch`, `sync_turn`, `on_session_end`, `on_pre_compress`, `on_memory_write`, `system_prompt_block`

---

## Install

**1. Install and start agentcache** (Python 3.10+):

```bash
pip install agentcache-core
agentcache serve --port 3111
```

(The distribution is `agentcache-core` on PyPI but installs the `agentcache` import + CLI.)

Health check: `curl http://localhost:3111/agentcache/health` should return a JSON payload with `"version": "0.9.11"`.

**2. Wire it into Hermes with a single command:**

```bash
agentcache connect hermes
```

This copies the plugin from the installed package into `~/.hermes/plugins/agentcache/`. Add `--force` to re-copy on top of an existing install. If you'd rather do it manually, the plugin lives at `<site-packages>/agentcache/integrations/hermes/`.

**3. Wire it in `~/.hermes/config.yaml`:**

```yaml
memory:
  provider: agentcache
```

Restart Hermes. `hermes memory status` should now list `agentcache` as available.

---

## Configuration

Environment variables (read at plugin import; anything you set in the shell wins):

| Variable | Default | Purpose |
|---|---|---|
| `AGENTCACHE_URL` | `http://localhost:3111` | agentcache server URL. |
| `AGENTCACHE_SECRET` | (unset) | Bearer token sent as `Authorization: Bearer …`. Must match `AGENTCACHE_SECRET` on the server. |
| `AGENTCACHE_REQUIRE_HTTPS` | (unset) | When `1`, refuse to send the secret over plaintext HTTP to a non-loopback host. With it off, the plugin still sends but prints a one-time warning on stderr. |

The plugin also preloads `~/.agentcache/.env` (or `$XDG_CONFIG_HOME/agentcache/.env`) via `os.environ.setdefault`, so `hermes memory status` still resolves the URL/secret when agentcache is launched by systemd or another process manager that never exports those values to the CLI shell.

---

## What each hook does

| Hook | Endpoint hit | Effect |
|---|---|---|
| `system_prompt_block()` | `POST /agentcache/context` | Injects the project profile block at session start |
| `prefetch(query)` | `POST /agentcache/smart-search` | Returns up to 5 relevant observations before the LLM call |
| `queue_prefetch(query)` | *(background)* | Warms the cache without blocking the turn |
| `sync_turn(user, assistant)` | `POST /agentcache/observe` | Fires-and-forgets every conversation turn as an observation |
| `on_pre_compress(messages)` | `POST /agentcache/context` | Re-injects context immediately before Hermes compacts history |
| `on_session_end(messages)` | `POST /agentcache/session/end` | Marks the session complete so background workers can summarise |
| `on_memory_write(action, …)` | `POST /agentcache/remember` | Mirrors `MEMORY.md` add/update writes into agentcache as memories |

## Tools exposed to the LLM

`memory_recall(query, limit=10)` — keyword search over observations.
`memory_save(content, type)` — persist an insight; `type ∈ {pattern, preference, architecture, bug, workflow, fact}`.
`memory_search(query, limit=5)` — hybrid BM25 + vector search across all memories.

All tool return values are JSON-encoded strings so Anthropic-protocol providers accept them as tool results.

---

## Alternative: MCP stdio instead of this plugin

If you'd rather skip the plugin layer, agentcache ships an MCP stdio bridge. Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  agentcache:
    command: python
    args: ["-m", "agentcache.mcp_stdio"]
```

You lose the six lifecycle hooks but keep the tool surface. The plugin form is stronger because it wires memory into the turn loop, not just into a tool namespace.

---

## Troubleshooting

- **`hermes memory status` shows Missing** — check `curl $AGENTCACHE_URL/agentcache/health`; if that works, the plugin isn't finding its config. Confirm `~/.agentcache/.env` is readable, or export `AGENTCACHE_URL` in the shell that launches Hermes.
- **401/403 on every call** — server has `AGENTCACHE_SECRET` set but the plugin doesn't; export a matching `AGENTCACHE_SECRET` in Hermes' environment.
- **Warned about plaintext bearer auth** — you're sending the secret over plain HTTP to a non-loopback host. Either terminate TLS in front of agentcache, tunnel over SSH, or unset the secret when talking to trusted local infrastructure.
- **Turns not being captured** — `sync_turn` is fire-and-forget; a dead server won't error the turn. Tail agentcache's stdout to confirm `/agentcache/observe` is receiving traffic.

---

## Maintainer & License

Maintained by [Yashwant K](mailto:yashwantk0303@gmail.com). MIT — same as the parent [agentcache](../../LICENSE) project.
