#!/usr/bin/env python
import sys
import json
import time
from hook_utils import is_sdk_child, api_call_bg

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

    api_call_bg("summarize", {"sessionId": session_id})
    api_call_bg("session/end", {"sessionId": session_id})

    # Allow the background threads to start their socket sends
    time.sleep(1.5)

if __name__ == "__main__":
    main()
