# Connecting agentcache to your coding agent

agentcache ships as a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so any MCP-aware coding agent can call its memory tools directly. This guide covers every client the `agentcache connect` CLI supports, plus manual wiring for anything else.

> **TL;DR** — in almost every case you just run:
>
> ```bash
> pip install agentcache-core
> agentcache serve --port 3111 &     # or run it as a service
> agentcache connect <agent-name>
> ```

## Requirements

- Python 3.10+
- `pip install agentcache-core` (ships the `agentcache` CLI and `agentcache.mcp_stdio` bridge)
- The agentcache HTTP server running locally (default `http://localhost:3111`)
- For each client below: the client itself installed and its config directory present

Environment variables the connect command reads:

| Variable | Default | Notes |
|---|---|---|
| `AGENTCACHE_URL` | `http://localhost:3111` | Server URL written into `env` of the MCP entry. |
| `AGENTCACHE_SECRET` | (unset) | Bearer token; if set, wired into the client's env. |
| `AGENTMEMORY_URL` / `AGENTMEMORY_SECRET` | — | Legacy fallbacks if the `AGENTCACHE_*` vars are unset. |

## Quick reference

| Client | Command | Config file it touches |
|---|---|---|
| Claude Code | `agentcache connect claude-code` | `~/.claude.json` |
| Codex CLI | `agentcache connect codex` | `~/.codex/config.toml` |
| Antigravity | `agentcache connect antigravity` | `~/Library/Application Support/Antigravity/User/mcp_config.json` (macOS), `%APPDATA%\Antigravity\User\mcp_config.json` (Windows), `~/.config/Antigravity/User/mcp_config.json` (Linux); also drops tool schemas under `~/.gemini/antigravity/mcp/agentcache/` |
| Kiro | `agentcache connect kiro` | `~/.kiro/settings/mcp.json` |
| VS Code | `agentcache connect vscode` | `~/Library/Application Support/Code/User/mcp.json` (macOS), `%APPDATA%\Code\User\mcp.json` (Windows), `~/.config/Code/User/mcp.json` (Linux) |
| Hermes Agent | `agentcache connect hermes` | copies plugin to `~/.hermes/plugins/agentcache/` (add `memory: { provider: agentcache }` to `~/.hermes/config.yaml`) |
| Cursor / Cline / Windsurf | `agentcache connect cursor` | writes `.cursorrules` / `.clineskills` / `.windsurfrules` in the current workspace |
| All detected clients | `agentcache connect --all` | wires every client whose config directory is present |

All commands accept:

- `--dry-run` — print what would change; do not touch disk.
- `--force` — overwrite an existing entry even when it matches.
- `--verify` — read-only diff: for each detected client (or the one you named), report whether its `agentcache` entry matches what `connect` would write, with per-field reasons when it doesn't. Never writes.
- `--with-hooks` *(Claude Code, Codex only)* — also install the workspace hook manifest.

## Behavior on re-run

`agentcache connect <agent>` compares the on-disk entry with what it would write and takes one of three actions:

- **Matches** → prints `[OK] <agent> already wired in <path> (re-run with --force to overwrite)` and exits.
- **Present but stale** (wrong Python interpreter path, wrong `mcp_stdio.py` path, missing required env keys) → backs the file up with a `.backup-<mtime>.<ext>` suffix and rewrites the entry. Prints `[OK] Updated existing agentcache MCP entry in <path>`.
- **Absent** → writes a fresh entry. Prints `[OK] Wired <agent> MCP …`.

This is deliberate: earlier releases silently reported `already wired` even when the entry pointed at a broken interpreter or stale checkout, and Claude Code (or whichever client) would then fail at startup. If you moved venvs or reinstalled the package, `agentcache connect` is now safe to re-run.

## Manual wiring (any MCP client)

Every entry the CLI writes has the same shape: run `mcp_stdio.py` under the current Python interpreter and set `AGENTCACHE_URL` in the child environment. You can hand-write this into any MCP client:

**JSON clients** (Claude Code, VS Code, Kiro, Antigravity, Cursor via MCP):

```json
{
  "mcpServers": {
    "agentcache": {
      "command": "/absolute/path/to/python",
      "args": ["/absolute/path/to/site-packages/agentcache/mcp_stdio.py"],
      "env": {
        "AGENTCACHE_URL": "http://localhost:3111",
        "AGENTCACHE_SECRET": "optional-bearer-token"
      }
    }
  }
}
```

Find those two paths quickly:

```bash
python -c "import sys, agentcache.mcp_stdio as m; print(sys.executable); print(m.__file__)"
```

**TOML clients** (Codex, and anything else using TOML MCP config):

```toml
[mcp_servers.agentcache]
command = "/absolute/path/to/python"
args = ["/absolute/path/to/site-packages/agentcache/mcp_stdio.py"]

[mcp_servers.agentcache.env]
AGENTCACHE_URL = "http://localhost:3111"
# AGENTCACHE_SECRET = "..."
```

**Module-invocation form** (works if the agent's Python has agentcache installed):

```bash
python -m agentcache.mcp_stdio
```

## Verifying the connection

1. **Server** — `curl http://localhost:3111/agentcache/health` should return `{"status": "ok", "version": "0.9.13", ...}`.
2. **Client** — start the agent and ask it to run `cache_smart_search(query="hello")`. If wiring succeeded you'll see a JSON tool result; if not, the client's MCP log (Claude Code: **View → Output → MCP Logs**) shows exactly what it tried to spawn.
3. **Interpreter mismatch** — if the tool call errors with `ModuleNotFoundError: No module named 'agentcache'`, the client is using a Python that doesn't have `agentcache-core` installed. Either `pip install agentcache-core` into that interpreter, or re-run `agentcache connect <agent> --force` from the interpreter you *do* want it to use.

## Uninstalling

There is no dedicated uninstall command. To remove the entry, delete `agentcache` (or `mcpServers.agentcache`, or the `[mcp_servers.agentcache]` block) from the config file listed in the table above. Each connect run leaves a timestamped `.backup-*` next to the file it modified, so you can revert to the previous state by restoring that backup.

## Related docs

- Server config and CLI: top-level `README.md`
- Auth and secrets: `AGENTS.md`
