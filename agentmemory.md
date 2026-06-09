# AgentMemory Automation & Client Hook Roundabout

This file documents the technical details and configurations for automating memory sessions, observations, and prompt-context syncing in environments that lack native client-side hook runners.

---

## 1. PowerShell-Level Console Hook Automation
For standard command shells that do not have custom plugins (such as PowerShell), we have implemented a native console hook system directly in the user profile:
* **Profile File**: `D:\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`
* **Automation Mechanism**:
  1. **Prompt Redefinition**: Overrides the default PowerShell `prompt` function to retrieve the last executed command from the session history (`Get-History`).
  2. **Asynchronous Dispatch**: Uses PowerShell's `Start-Job` to send the command, execution status, and duration to the Hugging Face Space database (`https://yash030-agentmemory-python.hf.space/agentmemory/observe`) in the background, keeping terminal prompts lag-free.
  3. **Interactive Shield**: Wraps the prompts and startup session creation in `if (-not [Console]::IsInputRedirected)` so background script executions or tool runners do not pollute the database.
  4. **Active Session Reuse**: On console startup, checks for a cached session ID in `current_session.txt` and reuses it to prevent session clutter.

---

## 2. Antigravity Chat Sync Loop
Because the Gemini Antigravity agent runs inside a transient prompt-response loop sandbox, we automate chat log syncing through a shell-level daemon job:
* **Sync Script**: `C:\Users\yashw\.gemini\antigravity\sync_antigravity.py`
* **Daemon Job**: Appended to the PowerShell profile as a background `Start-Job` loop named `AntigravitySync`.
* **Execution Heuristic**:
  - The job runs a continuous loop that executes the sync script every 30 seconds.
  - Uses a lock file (`sync_antigravity.lock`) to ensure only a single terminal window runs the sync globally.
  - The script parses `transcript.jsonl` from the local `brain/` directory and uploads new user prompts and response turns to the Hugging Face Space under the session `antigravity_<conversation_id>`.

---

## 3. Configuration & Controls
To control this automation inside PowerShell, use the following commands:
* **`Start-MemorySession -project "name"`**: Starts a new memory session. Use the `-Force` switch to override any active session.
* **`End-MemorySession`**: Ends the active session, marking it completed and cleaning up the local cache file.
* **`Get-Job -Name "AntigravitySync"`**: Checks the status of the background chat sync thread.
