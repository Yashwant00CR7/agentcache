#!/usr/bin/env python
import sys
import json
import time
from hook_utils import resolve_project, is_sdk_child, api_call, api_call_bg

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

    session_id = data.get("session_id") or data.get("sessionId") or f"ses_{int(time.time() * 1000)}"
    cwd = data.get("cwd") or ""
    project = resolve_project(cwd)

    inject_context = "--inject" in sys.argv
    import os
    if os.environ.get("AGENTMEMORY_INJECT_CONTEXT") == "true":
        inject_context = True

    payload = {
        "sessionId": session_id,
        "project": project,
        "cwd": cwd
    }

    if not inject_context:
        # Run asynchronously and fail fast
        api_call_bg("session/start", payload)
        # Sleep briefly to let the background thread start socket send
        time.sleep(0.1)
        return

    # In synchronous mode, await and write context to stdout
    result = api_call("session/start", payload, timeout=1.5)
    if result and result.get("context"):
        sys.stdout.write(result["context"])

if __name__ == "__main__":
    main()
