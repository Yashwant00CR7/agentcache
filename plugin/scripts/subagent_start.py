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

    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    agent_id = data.get("agent_id") or data.get("agentName")
    agent_type = data.get("agent_type") or data.get("agentDisplayName") or data.get("agentName")

    payload = {
        "hookType": "subagent_start",
        "sessionId": session_id,
        "project": resolve_project(data.get("cwd")),
        "cwd": data.get("cwd") or "",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "agent_id": agent_id,
            "agent_type": agent_type
        }
    }

    api_call_bg("observe", payload)
    time.sleep(0.5)

if __name__ == "__main__":
    main()
