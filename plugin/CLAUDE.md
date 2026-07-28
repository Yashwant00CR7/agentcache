# Plugin Directory — CLAUDE.md

## What This Is

The `plugin/` directory contains all agent-facing integration artifacts for agentmemory-python: hook scripts, MCP configs, skills, and automation utilities.

## Directory Layout

```
plugin/
├── plugin.json              Plugin manifest (name, version, hooks, skills, MCP)
├── antigravity.md           Antigravity-specific integration notes
├── CLAUDE.md                This file
├── .claude-plugin/
│   └── plugin.json          Claude Code plugin config
├── .codex-plugin/
│   └── plugin.json          Codex plugin config
├── .mcp.json                MCP server config (local)
├── .mcp.copilot.json        MCP server config (Copilot)
├── hooks/
│   ├── hooks.json           Claude Code hooks config
│   ├── hooks.codex.json     Codex hooks config
│   └── hooks.copilot.json   Copilot hooks config
├── scripts/                 Python hook scripts (see below)
└── skills/                  Agent skill definitions
```

## Hook Scripts (`plugin/scripts/`)

| Script | Hook Event | Purpose |
|--------|-----------|---------|
| `session_start.py` | `session_start` | Register new session, optionally inject context |
| `session_end.py` | `session_end` | Mark session complete, trigger consolidation |
| `prompt_submit.py` | `prompt_submit` | Log user prompt as observation |
| `pre_tool_use.py` | `PreToolUse` | Enrich file context before file tools (read/edit/write) |
| `post_tool_use.py` | `PostToolUse` | Log tool execution as observation |
| `post_tool_failure.py` | `PostToolUse` (failure) | Log failed tool calls |
| `pre_compact.py` | `PreCompact` | Sync memory before context compaction |
| `subagent_start.py` | `session_start` (SDK child) | Subagent session start |
| `subagent_stop.py` | `session_end` (SDK child) | Subagent session end |
| `stop.py` | `Stop` | Flush session memory (summarize) + end session |
| `task_completed.py` | `PostToolUse` (task done) | Log task completion |
| `notification.py` | Various | Desktop/push notifications on events |
| `notify.py` | (helper) | `confirm_flush(project)` — optional Save/Skip confirm before a flush (macOS dialog; Linux heads-up only) |

### Automation Scripts (non-hook)

| Script | Purpose |
|--------|---------|
| `auto_session_start.py` | **Upsert session**: checks if session exists in agentmemory, updates if found, creates if not. Use as drop-in replacement for `session_start.py` when hooks don't fire. |
| `auto_log.py` | Log a single observation via MCP tools directly |
| `auto_log_prompt.py` | Log a user prompt via MCP tools directly |
| `mcp_stdio.py` | MCP stdio bridge — reads `AGENTMEMORY_URL` and `AGENTMEMORY_SECRET` from env |
| `simple_test_hook.py` | Debug utility — logs invocation to `~/.agentmemory/hook_test_log.txt` |

### Key shared utility: `hook_utils.py`

Provides:
- `load_env()` — loads `~/.agentmemory/.env` at import time
- `resolve_project(cwd)` — git root basename or cwd basename
- `is_sdk_child(payload)` — detects SDK subagent invocations
- `api_call(path, body, timeout)` — sync REST call to agentmemory
- `api_call_bg(path, body)` — background thread REST call

## Flush / Pull Memory Pipeline (Claude Code hooks only)

> **Scope warning:** The *flush* and *pull* steps described here are specific to
> the **Claude Code** hook lifecycle (`Stop`, `PreCompact`, `SessionEnd`). Other
> harnesses — **Cursor, Cline, Kiro, Codex** — integrate through
> **continuous capture only** (each tool call is logged live via `agent_observe`
> / MCP). They do **not** run the flush/pull pipeline, because they have no
> equivalent Stop/PreCompact hook to trigger it. Do not assume this behaviour
> exists outside Claude Code.

The pipeline runs entirely on the **folder-scoped** memory model. Every step
maps the hook payload to a `(folderPath, agentId)` scope using the same identity
convention as `agent_observe`:

```
folderPath = cwd or project      agentId = sessionId
```

**Flush** (`Stop` → `POST /summarize`, and `PreCompact` before its pull):
1. Read every observation after the folder's stored **flush cursor**
   (`ObservationStore.observations_since`).
2. Map-reduce them into a summary via Gemini and store it in the folder's
   metadata (`folder_meta → meta["summary"]`).
3. Advance the flush cursor to the newest observation so the same observations
   are never summarized twice.
4. If `GEMINI_API_KEY` is unset, `/summarize` no-ops with
   `{"success": false, "error": "GEMINI_API_KEY is not set"}` — the flush is
   skipped, never fatal.

**Pull** (`PreCompact` → `POST /context`): builds an injected context block from
prior folder summaries → relevant long-term memories → recent high-signal
observations, greedily packed into a character budget.

**Flush-before-pull:** `pre_compact.py` calls `/summarize` *before* `/context`,
so the context injected at compaction reflects the latest work rather than a
stale summary.

**Consolidate** (`SessionEnd` → `POST /consolidate-pipeline`,
`POST /crystals/auto`): iterates `KV.folders` / `folder_obs` (no longer sessions)
to synthesize cross-folder long-term memories, semantic facts, and procedures.
Gated by `CONSOLIDATION_ENABLED`.

Hook failures are no longer silent: `hook_utils.api_call` logs `http_error`
(e.g. a 404 from a missing route) distinctly from `network_error` (server down)
to `~/.agentcache/hooks.log` (override with `AGENTCACHE_HOOK_LOG`).

## Environment Variables

All scripts read from environment (or `~/.agentmemory/.env`):

| Variable | Purpose |
|----------|---------|
| `AGENTMEMORY_URL` | Base URL of agentmemory server (default: `http://localhost:3111`) |
| `AGENTMEMORY_SECRET` | Bearer token for auth |
| `AGENTMEMORY_PROJECT` | Override project name (default: git root basename) |
| `AGENTMEMORY_SESSION_ID` | Override session ID |
| `AGENTMEMORY_CWD` | Override working directory |
| `AGENTMEMORY_INJECT_CONTEXT` | `true` to inject context into stdout on session start |
| `AGENTMEMORY_AGENT_ID` | Agent identifier (default: `claude-code`) |
| `CONSOLIDATION_ENABLED` | `true` to run consolidation on session end |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini key for `/summarize` + consolidation (flush no-ops without it) |
| `AGENTCACHE_FLUSH_CONFIRM` | `true` to prompt Save/Skip before a flush (macOS dialog; Linux heads-up only). Skip suppresses that flush; unsupported/errored falls back to silent proceed (`notify.py`) |
| `AGENTCACHE_HOOK_LOG` | Override path for the hook error log (default `~/.agentcache/hooks.log`) |

## Skills (`plugin/skills/`)

| Skill | Purpose |
|-------|---------|
| `agentmemory-agents` | How agents should interact with agentmemory |
| `agentmemory-architecture` | Architecture overview for agents |
| `agentmemory-config` | Configuration reference |
| `agentmemory-hooks` | Hooks system reference |
| `agentmemory-mcp-tools` | MCP tools reference (20 tools) |
| `agentmemory-rest-api` | REST API reference |
| `commit-context` | Save git commit context to memory |
| `commit-history` | Recall commit history from memory |
| `forget` | Delete observations/sessions/memories |
| `handoff` | Summarize session for handoff |
| `recall` | Search past observations |
| `recap` | Summarize current session |
| `remember` | Save insight to long-term memory |
| `session-history` | View session observation history |
| `write-agentmemory-skill` | Meta-skill: create new agentmemory skills |

## How Hooks Were Wired (History)

Originally hooks were registered in `~/.claude.json` under `"hooks"` key:

```json
{
  "hooks": {
    "session_start": { "command": "python", "args": ["...session_start.py"], "env": {...} },
    "session_end": { "command": "python", "args": ["...session_end.py"], "env": {...} },
    "prompt_submit": { "command": "python", "args": ["...prompt_submit.py"], "env": {...} }
  }
}
```

**Issue discovered (2026-06-10):** Claude Code hooks were not being invoked automatically. `prompt_submit` hook never fired — `~/.agentmemory/hook_log.txt` was never created. Hooks removed from config.

**Working alternative:** Use `auto_session_start.py` directly with env vars set. This uses the REST API + MCP tools to create/update sessions without relying on hooks.

## Auto-Session Upsert Logic (`auto_session_start.py`)

1. Calls `memory_sessions_list` MCP tool to get all sessions
2. Searches for session matching `AGENTMEMORY_SESSION_ID`
3. If found → logs "Session reactivated" observation via `agent_observe`
4. If not found → calls `POST /session/start` REST endpoint to create new session
5. Returns session info + context

## MCP Configuration

The `agentmemory-python` MCP is configured in `~/.claude.json`:

```json
{
  "agentmemory-python": {
    "type": "stdio",
    "command": "python",
    "args": ["D:\\Downloads\\Projects\\Other Projects\\agentmemory-python\\plugin\\scripts\\mcp_stdio.py"],
    "env": {
      "AGENTMEMORY_URL": "https://yash030-agentmemory-python.hf.space",
      "AGENTMEMORY_SECRET": "test"
    }
  }
}
```

`mcp_stdio.py` bridges Claude Code's MCP stdio protocol to the Flask HTTP API.
