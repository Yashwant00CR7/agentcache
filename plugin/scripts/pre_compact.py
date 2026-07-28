#!/usr/bin/env python
import sys
import os
import json
from hook_utils import resolve_project, is_sdk_child, api_call
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

    if os.environ.get("CLAUDE_MEMORY_BRIDGE") == "true":
        api_call("claude-bridge/sync", {}, timeout=5.0)

    # Optional Save/Skip confirm (AGENTCACHE_FLUSH_CONFIRM). Skip suppresses the
    # flush for this PreCompact event; the context pull below still proceeds.
    # Disabled / unsupported / errored → proceeds silently (returns True).
    if confirm_flush(project):
        # Flush-before-pull: fold any un-summarized observations into the folder
        # summary *before* building context, so the injected context reflects the
        # latest work rather than a stale summary. Best-effort — if summarize is a
        # no-op (no key / no new observations) the pull still proceeds.
        api_call("summarize", {"sessionId": session_id, "project": project, "cwd": cwd}, timeout=8.0)

    result = api_call("context", {"sessionId": session_id, "project": project, "cwd": cwd, "budget": 1500}, timeout=5.0)
    if result and result.get("context"):
        sys.stdout.write(result["context"])

if __name__ == "__main__":
    main()
