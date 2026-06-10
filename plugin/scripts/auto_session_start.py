#!/usr/bin/env python3
"""
Auto-session start script - uses MCP tools to create/start session.
If session exists, it updates; if not, it creates new.
"""
import os
import sys
import json
import urllib.request
import uuid
from datetime import datetime, timezone

# Configuration from environment
AGENTMEMORY_URL = os.environ.get('AGENTMEMORY_URL', 'http://localhost:3111')
AGENTMEMORY_SECRET = os.environ.get('AGENTMEMORY_SECRET')
AGENTMEMORY_PROJECT = os.environ.get('AGENTMEMORY_PROJECT', os.path.basename(os.getcwd()))
AGENTMEMORY_CWD = os.environ.get('AGENTMEMORY_CWD', os.getcwd())
AGENTMEMORY_SESSION_ID = os.environ.get('AGENTMEMORY_SESSION_ID')
AGENTMEMORY_AGENT_ID = os.environ.get('AGENTMEMORY_AGENT_ID', 'claude-code')

def get_mcp_url():
    """Get MCP tools endpoint URL."""
    base = AGENTMEMORY_URL.rstrip('/')
    if not base.endswith('/agentmemory'):
        base = f"{base}/agentmemory"
    return f"{base}/mcp/tools"

def get_rest_url():
    """Get REST API base URL."""
    base = AGENTMEMORY_URL.rstrip('/')
    if not base.endswith('/agentmemory'):
        base = f"{base}/agentmemory"
    return base

def headers():
    """Build request headers."""
    h = {"Content-Type": "application/json"}
    if AGENTMEMORY_SECRET:
        h["Authorization"] = f"Bearer {AGENTMEMORY_SECRET}"
    return h

def call_mcp_tool(tool_name, arguments):
    """Call MCP tool and return response."""
    payload = {"name": tool_name, "arguments": arguments}
    req = urllib.request.Request(
        get_mcp_url(),
        data=json.dumps(payload).encode('utf-8'),
        headers=headers(),
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def call_rest_api(path, method="GET", data=None):
    """Call REST API and return response."""
    url = f"{get_rest_url()}/{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8') if data else None,
        headers=headers(),
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def start_session(session_id, project, cwd, agent_id=None):
    """Start or update session via MCP tools."""
    # Get sessions list
    sessions_result = call_mcp_tool("memory_sessions_list", {})
    sessions = []

    # Parse response: {"content": [{"text": "{\"sessions\": [...]}" }]}
    try:
        content_text = sessions_result.get('content', [{}])[0].get('text', '[]')
        sessions_data = json.loads(content_text)
        sessions = sessions_data.get('sessions', []) if isinstance(sessions_data, dict) else []
    except Exception as e:
        print(f"[auto_session_start] Error parsing sessions: {e}", file=sys.stderr)
        sessions = []

    # Check if session exists
    existing_session = None
    for s in sessions:
        if s.get('id') == session_id:
            existing_session = s
            break

    if existing_session:
        # Session exists - update it by logging an observation
        obs_result = call_mcp_tool("agent_observe", {
            "sessionId": session_id,
            "project": project,
            "cwd": cwd,
            "text": f"Session reactivated at {datetime.now(timezone.utc).isoformat()}Z",
            "type": "thought",
            "title": "Session Reactivated"
        })
        return {
            "action": "updated",
            "sessionId": session_id,
            "message": "Session updated - reactivated via observation",
            "observation": obs_result
        }
    else:
        # Session doesn't exist - create via REST API
        url = f"{get_rest_url()}/session/start"
        payload = {
            "sessionId": session_id,
            "project": project,
            "cwd": cwd,
            "agentId": agent_id,
            "title": f"Session started at {datetime.now(timezone.utc).isoformat()}Z"
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers(),
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return {
                    "action": "created",
                    "sessionId": session_id,
                    "result": result
                }
        except Exception as e:
            return {
                "action": "failed",
                "sessionId": session_id,
                "error": str(e)
            }

def main():
    # Get or generate session ID
    session_id = AGENTMEMORY_SESSION_ID or f"claude-{uuid.uuid4().hex[:12]}"

    result = start_session(
        session_id=session_id,
        project=AGENTMEMORY_PROJECT,
        cwd=AGENTMEMORY_CWD,
        agent_id=AGENTMEMORY_AGENT_ID
    )

    print(json.dumps({
        "session_id": session_id,
        "project": AGENTMEMORY_PROJECT,
        "cwd": AGENTMEMORY_CWD,
        "result": result
    }))

if __name__ == "__main__":
    main()
