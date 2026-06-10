#!/usr/bin/env python
import sys
import json
import time
import os
from datetime import datetime, timezone
from hook_utils import resolve_project, is_sdk_child, api_call_bg

def main():
    # Log to file for debugging - write early before any potential errors
    log_path = os.path.expanduser("~/.agentmemory/hook_log.txt")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] prompt_submit hook called\n")
            f.write(f"sys.argv: {sys.argv}\n")
    except Exception as e:
        pass

    try:
        input_data = sys.stdin.read()
        if not input_data:
            return
        data = json.loads(input_data)
    except Exception as e:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Error parsing stdin: {e}\n")
        except:
            pass
        return

    if is_sdk_child(data):
        return

    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    cwd = data.get("cwd") or ""
    project = resolve_project(cwd)
    prompt = data.get("prompt") or data.get("userPrompt") or ""

    payload = {
        "hookType": "prompt_submit",
        "sessionId": session_id,
        "project": project,
        "cwd": cwd,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": {
            "prompt": prompt
        }
    }

    api_call_bg("observe", payload)
    # Allow background request to be flushed
    time.sleep(0.5)

if __name__ == "__main__":
    main()
