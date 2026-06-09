#!/usr/bin/env python
import sys
import os
import json
from hook_utils import resolve_project, is_sdk_child, api_call

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
    project = resolve_project(data.get("cwd"))

    if os.environ.get("CLAUDE_MEMORY_BRIDGE") == "true":
        api_call("claude-bridge/sync", {}, timeout=5.0)

    result = api_call("context", {"sessionId": session_id, "project": project, "budget": 1500}, timeout=5.0)
    if result and result.get("context"):
        sys.stdout.write(result["context"])

if __name__ == "__main__":
    main()
