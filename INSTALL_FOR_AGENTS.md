# Install agentmemory-python (agent runbook)

You are a coding agent. Follow this runbook top to bottom to install agentmemory-python, wire it into yourself, and verify that persistent memory works. Run each command, check the result against the "Expect" note, then move on.

## What you are installing

agentmemory-python is a local memory server for coding agents. It runs on your machine, exposes a REST API on port 3111 and an MCP endpoint, and stores all memories in a single SQLite file at `~/.agentmemory/agentmemory.db`. It is built with Python and Flask — no Node.js, no external database, no daemon to manage separately.

Default mode works with no API key. It runs BM25 keyword search out of the box. Set `GEMINI_API_KEY` to also enable vector semantic search.

## Prerequisites

- Python 3.10 or newer. Check with `python --version` or `python3 --version`.
- pip. Check with `pip --version`.
- Port 3111 free. If taken, set `III_REST_PORT=<other>` in `~/.agentmemory/.env`.

## 1. Clone the repo

```bash
git clone https://github.com/Yash030/agentmemory-python.git
cd agentmemory-python
```

Expect: the directory exists with `src/app.py` inside.

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

This installs: flask, flask-sock, requests, websockets, python-dateutil, huggingface_hub.

Expect: install completes without errors.

## 3. Start the server

```bash
python src/app.py &
```

Or run in a separate terminal: `python src/app.py`

Wait until it is reachable:

```bash
curl -fsS http://localhost:3111/agentmemory/livez
```

Expect: `{"status": "ok"}`. On first run the BM25 index initializes in the background; this takes under a second.

## 4. Prove save and recall works

```bash
# Save a memory
curl -X POST http://localhost:3111/agentmemory/remember \
  -H "Content-Type: application/json" \
  -d '{"content":"agentmemory-python install verification probe","concepts":["install-check"]}'

# Recall it
curl -X POST http://localhost:3111/agentmemory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"install verification probe","limit":5}'
```

Expect: first call returns `{"id": "...", "success": true}`, second returns results containing the probe memory.

## 5. Wire MCP into the calling agent

Detect which agent is running this runbook, then add agentmemory-python to its MCP config.

### Standard MCP block (Cursor, Claude Desktop, Cline, Windsurf, Gemini CLI)

Merge this into the agent's `mcpServers` object:

```json
"agentmemory": {
  "command": "npx",
  "args": ["-y", "@agentmemory/mcp"],
  "env": {
    "AGENTMEMORY_URL": "http://localhost:3111"
  }
}
```

### Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
"agentmemory": {
  "command": "npx",
  "args": ["-y", "@agentmemory/mcp"],
  "env": {
    "AGENTMEMORY_URL": "http://localhost:3111"
  }
}
```

Then reload MCP: run `/mcp` in Claude Code.

### Any agent — verify tool count

After wiring, the agent should list agentmemory's tools. With the server running you should see 16 tools (e.g. `memory_save`, `memory_smart_search`, `memory_sessions`).

If you see 0 tools or an error, check that `python src/app.py` is running and `AGENTMEMORY_URL` points at it.

## 6. Setting up agent hooks

Agent hooks post observations to agentmemory automatically on every tool use, command, or edit — no manual calls required. Hook scripts live in the [`hooks/`](hooks/) directory.

### Claude Code (`hooks/claude-code-hook.sh`)

Add a `PostToolUse` hook to `.claude/settings.json`. The hook script reads `AGENTMEMORY_URL` and `AGENTMEMORY_SECRET` from your shell environment, so no secrets are embedded in the config file.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/hooks/claude-code-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Set the required environment variables before starting Claude Code:

```bash
export AGENTMEMORY_URL=http://127.0.0.1:3111
export AGENTMEMORY_SECRET=your-secret-here   # omit if no auth set
```

The script picks up `$PWD` as `folderPath` and `$CLAUDE_AGENT_ID` (falling back to `"claude-code"`) as `agentId`.

### Cursor (`hooks/cursor-hook.js`)

Require the hook from your `.cursorrules` file (or any JS entry point Cursor runs) and call `logObservation()` with the tool name and input:

```js
const { logObservation } = require('/path/to/hooks/cursor-hook.js');

// Call inside your Cursor hook handler, e.g. after every tool invocation:
logObservation(`Tool: ${toolName}\nInput: ${JSON.stringify(toolInput)}`);
```

The module reads `AGENTMEMORY_URL`, `AGENTMEMORY_SECRET`, and `AGENTMEMORY_AGENT_ID` from `process.env`. Set them in your shell profile or in Cursor's environment settings.

### PowerShell terminal (`hooks/powershell-hook.ps1`)

Add a single dot-source line to your PowerShell `$PROFILE` to activate automatic command logging:

```powershell
. C:\path\to\hooks\powershell-hook.ps1
```

Set the required variables in `$PROFILE` before the dot-source line:

```powershell
$env:AGENTMEMORY_URL      = "http://127.0.0.1:3111"
$env:AGENTMEMORY_SECRET   = "your-secret-here"   # omit if no auth set
$env:AGENTMEMORY_AGENT_ID = "powershell"
```

The hook installs a PSReadLine `CommandValidationHandler` that fires a background job on every command you run. If PSReadLine is not available, call `Send-AgentMemoryObservation -Text "..."` manually.

### `.env` file format

All hooks and the server itself read credentials from `~/.agentmemory/.env`. Create this file if it doesn't exist:

```
III_REST_PORT=3111
AGENTMEMORY_SECRET=your-secret-here
GEMINI_API_KEY=your-gemini-key-here
```

The server loads this file on startup. Hook scripts read the same variables from your shell environment (export them from your profile after sourcing `~/.agentmemory/.env`, or use `direnv` / `dotenv` tooling).

## 7. Open the viewer (optional)

```bash
open http://localhost:3111/viewer
# or on Linux:
xdg-open http://localhost:3111/viewer
# or on Windows:
start http://localhost:3111/viewer
```

The viewer shows live sessions, memories, and the knowledge graph.

## Optional: enable vector search

Vector search finds memories semantically, not just by keyword. Enable it with a free Gemini API key:

```bash
mkdir -p ~/.agentmemory
echo "GEMINI_API_KEY=your-key-here" >> ~/.agentmemory/.env
```

Restart the server after adding the key. The viewer's search bar will now use hybrid BM25 + vector retrieval.

## Optional: enable LLM compression

LLM compression makes each observation richer and more searchable. Requires an LLM API key:

```bash
# Add to ~/.agentmemory/.env
ANTHROPIC_API_KEY=your-key   # or OPENAI_API_KEY or GEMINI_API_KEY
AGENTMEMORY_AUTO_COMPRESS=true
```

## Lifecycle

```bash
# Stop the server (find and kill the process)
pkill -f "python src/app.py"

# Check what's stored
curl http://localhost:3111/agentmemory/sessions
curl http://localhost:3111/agentmemory/memories

# Health check
curl http://localhost:3111/agentmemory/livez
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'flask'` | Run `pip install -r requirements.txt` |
| `Address already in use` on port 3111 | Set `III_REST_PORT=3112` in `~/.agentmemory/.env` and restart |
| `/livez` never returns | Check `python src/app.py` stderr for import errors |
| 0 tools in agent | Confirm `AGENTMEMORY_URL=http://localhost:3111` and the server is running |
| `401 Unauthorized` | Set `AGENTMEMORY_SECRET` env var in the MCP config to match `~/.agentmemory/.env` |
| Vector search not working | Add `GEMINI_API_KEY` to `~/.agentmemory/.env` and restart |
| Windows Python path issue | Use `python3` instead of `python`, or use WSL2 |

## Report success

Report back:

- agentmemory-python server running on port 3111
- MCP wired into the agent, tool count visible
- Save and recall round-trip returned the probe memory
- Viewer available at http://localhost:3111/viewer
- Any optional features enabled (vector search, LLM compression)

If any step failed, report the step, exact command, and error output.
