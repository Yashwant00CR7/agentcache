#!/usr/bin/env python
import sys
import json
import time
from hook_utils import is_sdk_child, api_call_bg, resolve_project
from notify import confirm_flush

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data:
            return
        data = json.loads(input_data)
    except Exception:
        return

    if is_sdk_child(data):
        return

    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    cwd = data.get("cwd")
    project = resolve_project(cwd)

    # Optional Save/Skip confirm (AGENTCACHE_FLUSH_CONFIRM). Skip suppresses the
    # flush; the session still ends. Disabled/unsupported → proceeds (True).
    if confirm_flush(project):
        # Flush new observations into a folder-scoped summary. folderPath = cwd or
        # project, agentId = sessionId — the agent_observe identity convention.
        api_call_bg("summarize", {"sessionId": session_id, "project": project, "cwd": cwd})
    api_call_bg("session/end", {"sessionId": session_id})

    # Allow the background threads to start their socket sends
    time.sleep(1.5)

if __name__ == "__main__":
    main()
