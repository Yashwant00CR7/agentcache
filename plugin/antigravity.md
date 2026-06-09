# Antigravity Memory Integration (The Shell-Level Hook Automation)

This document details the background syncing and terminal prompt automation workaround implemented to support seamless long-term memory capture and context injection for the **Gemini Antigravity** coding agent.

---

## The Challenge
Unlike client agents like Claude Code or Codex, which have built-in client configuration hook runners (like `UserPromptSubmit` or `PreToolUse`), Antigravity runs inside a transient, reactive sandbox without any native client-side hooks. This makes it impossible for the agent itself to trigger startup/shutdown registration or command-level logging automatically.

---

## The Solution (The Roundabout)
To achieve seamless, zero-reminder automation, we moved the integration logic from the agent sandbox into the **local shell environment itself**.

```
[ PowerShell Console ]
 ├── Prompt Loop Hook (Asynchronous post-execution capture)
 ├── Startup Auto-Register (Detects Git project context & starts session)
 └── Background Sync Job (Polls and uploads transcript.jsonl every 30s)
            │
            ▼
 [ Hugging Face Space Database ] (https://yash030-agentmemory-python.hf.space)
```

### 1. Active Chat Syncing (`sync_antigravity.py`)
Located in the local application folder (`C:\Users\yashw\.gemini\antigravity\sync_antigravity.py`), this script runs outside the sandbox to bridge chat logs into the memory space:
* **Transcript Parsing**: Reads the active conversation logs (`brain/<conversation_id>/.system_generated/logs/transcript.jsonl`).
* **Observation Registry**: Formats and POSTs new user prompts and responses as observations under the session key `antigravity_<conversation_id>`.
* **State Verification**: Checks existing observations first to ensure no duplicates are uploaded.

### 2. PowerShell Profile Integration (`Microsoft.PowerShell_profile.ps1`)
To automate execution, we injected the sync loops and shell prompt captures directly into your PowerShell profile.

#### A. Interactive Check
```powershell
if (-not [Console]::IsInputRedirected) {
    # Define prompt hooks and background jobs here
}
```
Ensures that prompt redefinitions and auto-start logic only run in real interactive terminals, completely preventing script runs or tool runners from spamming the database.

#### B. Active Session Reuse
```powershell
if (-not $Force -and (Test-Path $global:agentMemorySessionFile)) {
    $sessionId = Get-Content $global:agentMemorySessionFile -Raw
    if ($sessionId) {
        Write-Host "[AgentMemory] Reusing active session: $sessionId" -ForegroundColor Cyan
        return
    }
}
```
Checks for an existing session ID in a local cache file (`current_session.txt`) on startup and reuses it. This prevents the creation of duplicate session IDs when opening multiple terminal tabs.

#### C. Async Console Command Capturing
```powershell
function prompt {
    $lastCommand = Get-History -Count 1
    # ... format and POST to /observe in background job ...
}
```
Redefines the terminal prompt to asynchronously log the execution status, command line, and duration of every command to the database in background threads, keeping terminal performance instantaneous.

#### D. Global Lock File Check
```powershell
Start-Job -Name "AntigravitySync" -ScriptBlock {
    # Check lock file modification time
    # If active instance exists, exit; otherwise, run sync_antigravity.py loop every 30s
}
```
Uses a timestamp-based lock file (`sync_antigravity.lock`) so that only a single terminal window runs the background sync job globally, preventing CPU and network overhead.
