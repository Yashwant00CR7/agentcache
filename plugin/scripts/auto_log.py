#!/usr/bin/env python3
"""
Auto-log observations using MCP tools directly.
This is a fallback when hooks don't work.
"""
import os
import sys
import json
import subprocess
import time

def get_current_session():
    """Get current session info from stdin or environment."""
    try:
        data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
        return data.get('session_id') or data.get('sessionId') or os.environ.get('AGENTMEMORY_SESSION_ID', 'unknown')
    except:
        return 'unknown'

def get_cwd():
    """Get current working directory."""
    return os.environ.get('AGENTMEMORY_CWD') or os.getcwd()

def get_project():
    """Get project name."""
    return os.environ.get('AGENTMEMORY_PROJECT') or os.path.basename(os.getcwd())

def send_observation(session_id, text, obs_type="thought"):
    """Send observation using MCP tool call."""
    try:
        agentmemory_url = os.environ.get('AGENTMEMORY_URL', 'http://localhost:3111')
        agentmemory_secret = os.environ.get('AGENTMEMORY_SECRET')

        base = agentmemory_url.rstrip('/').replace('/agentmemory', '')
        mcp_url = f"{base}/agentmemory/mcp/tools"

        payload = {
            "name": "agent_observe",
            "arguments": {
                "sessionId": session_id,
                "project": get_project(),
                "cwd": get_cwd(),
                "text": text,
                "type": obs_type
            }
        }

        import urllib.request
        headers = {"Content-Type": "application/json"}
        if agentmemory_secret:
            headers["Authorization"] = f"Bearer {agentmemory_secret}"

        req = urllib.request.Request(
            mcp_url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def main():
    session_id = get_current_session()
    cwd = get_cwd()
    project = get_project()

    # Send observation
    result = send_observation(
        session_id,
        f"Session started - project: {project}, cwd: {cwd}",
        "thought"
    )

    print(json.dumps({"session_id": session_id, "result": result}))

if __name__ == "__main__":
    main()
