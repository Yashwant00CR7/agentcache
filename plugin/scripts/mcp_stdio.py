#!/usr/bin/env python3
"""
stdio MCP wrapper — bridges Claude Code's MCP stdio protocol to the
agentmemory Flask HTTP API running on localhost.

Usage: python mcp_stdio.py
"""
import sys
import json
import requests

import os
BASE = os.getenv("AGENTMEMORY_URL", "http://127.0.0.1:3111").rstrip("/")
if not BASE.endswith("/agentmemory"):
    BASE = f"{BASE}/agentmemory"

_secret = os.getenv("AGENTMEMORY_SECRET")

def headers():
    h = {"Content-Type": "application/json"}
    if _secret:
        h["Authorization"] = f"Bearer {_secret}"
    return h

def send(msg):
    line = json.dumps(msg, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

def handle(req):
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentmemory-local", "version": "0.9.8"}
            }
        })

    elif method == "initialized":
        pass  # notification — no response

    elif method == "ping":
        send({"jsonrpc": "2.0", "id": req_id, "result": {}})

    elif method == "tools/list":
        try:
            r = requests.get(f"{BASE}/mcp/tools", headers=headers(), timeout=5)
            tools = r.json().get("tools", [])
            send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": req_id,
                  "error": {"code": -32000, "message": f"agentmemory unreachable: {e}"}})

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            r = requests.post(f"{BASE}/mcp/tools",
                              headers=headers(),
                              json={"name": name, "arguments": args},
                              timeout=30)
            result = r.json()
            # MCP expects content array
            if "content" not in result:
                result = {"content": [{"type": "text", "text": json.dumps(result)}]}
            send({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": req_id,
                  "error": {"code": -32000, "message": str(e)}})

    elif req_id is not None:
        send({"jsonrpc": "2.0", "id": req_id,
              "error": {"code": -32601, "message": "Method not found"}})


def main():
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        try:
            handle(req)
        except Exception as e:
            req_id = req.get("id") if isinstance(req, dict) else None
            if req_id is not None:
                send({"jsonrpc": "2.0", "id": req_id,
                      "error": {"code": -32603, "message": f"Internal error: {e}"}})

if __name__ == "__main__":
    main()
