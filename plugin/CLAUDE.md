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
| `stop.py` | `Stop` | Final cleanup on Claude exit |
| `task_completed.py` | `PostToolUse` (task done) | Log task completion |
| `notification.py` | Various | Desktop/push notifications on events |

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
