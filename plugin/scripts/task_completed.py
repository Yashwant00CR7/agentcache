#!/usr/bin/env python
import sys
import json
import time
from datetime import datetime
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
    desc = data.get("task_description") or ""
    if isinstance(desc, str):
        desc = desc[:2000]
    else:
        desc = ""

    payload = {
        "hookType": "task_completed",
        "sessionId": session_id,
        "project": resolve_project(data.get("cwd")),
        "cwd": data.get("cwd") or "",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": {
            "task_id": data.get("task_id"),
            "task_subject": data.get("task_subject"),
            "task_description": desc,
            "teammate_name": data.get("teammate_name"),
            "team_name": data.get("team_name")
        }
    }

    api_call_bg("observe", payload)
    time.sleep(0.5)

if __name__ == "__main__":
    main()
