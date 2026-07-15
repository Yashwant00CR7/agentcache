#!/usr/bin/env python
import sys
import json
import time
from datetime import datetime, timezone
from hook_utils import resolve_project, is_sdk_child, api_call_bg

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
    if data.get("is_interrupt") or data.get("isInterrupt"):
        return

    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    tool_name = data.get("tool_name") or data.get("toolName")
    tool_input = data.get("tool_input") or data.get("toolArgs")
    error = data.get("error") or data.get("errorMessage")

    def limit_str(val):
        if not isinstance(val, str):
            val = json.dumps(val or "")
        return val[:4000]

    payload = {
        "hookType": "post_tool_failure",
        "sessionId": session_id,
        "project": resolve_project(data.get("cwd")),
        "cwd": data.get("cwd") or "",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "tool_name": tool_name,
            "tool_input": limit_str(tool_input),
            "error": limit_str(error)
        }
    }

    api_call_bg("observe", payload)
    # Allow background request to be flushed
    time.sleep(0.5)

if __name__ == "__main__":
    main()
