#!/usr/bin/env python3
"""Simple hook test - writes to file on ANY invocation."""
import os
import sys
import json
import time
from datetime import datetime, timezone

# Write to a very specific location that will be easy to find
log_file = r"C:\Users\yashw\.agentmemory\hook_test_log.txt"

try:
    with open(log_file, "a", encoding="utf-8") as f:
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"\n=== Hook called at {ts} ===\n")
        f.write(f"Command: {sys.argv[0]}\n")
        f.write(f"Args: {sys.argv[1:]}\n")
        f.write(f"Stdin: {sys.stdin.read()[:1000]}\n")
        f.write(f"Env AGENTMEMORY_URL: {'SET' if os.environ.get('AGENTMEMORY_URL') else 'NOT SET'}\n")
except Exception as e:
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"Error: {e}\n")
    except:
        pass
