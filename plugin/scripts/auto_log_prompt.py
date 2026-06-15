#!/usr/bin/env python3
"""
Auto-log user prompt to agentmemory using MCP tools.
Usage: Called on prompt submit - receives prompt data via stdin
"""
import os
import sys
import json
import urllib.request

# Configuration from environment
AGENTMEMORY_URL = os.environ.get('AGENTMEMORY_URL', 'http://localhost:3111')
AGENTMEMORY_SECRET = os.environ.get('AGENTMEMORY_SECRET')
AGENTMEMORY_PROJECT = os.environ.get('AGENTMEMORY_PROJECT')
AGENTMEMORY_SESSION_ID = os.environ.get('AGENTMEMORY_SESSION_ID')
AGENTMEMORY_CWD = os.environ.get('AGENTMEMORY_CWD')

def get_base_url():
    """Get base URL with /agentmemory path."""
    base = AGENTMEMORY_URL.rstrip('/')
    if not base.endswith('/agentmemory'):
        base = f"{base}/agentmemory"
    return base

def headers():
    """Build request headers with optional auth."""
    h = {"Content-Type": "application/json"}
    if AGENTMEMORY_SECRET:
        h["Authorization"] = f"Bearer {AGENTMEMORY_SECRET}"
    return h

def send_observation(session_id, text, obs_type="thought"):
    """Send observation via MCP tools endpoint."""
    base = get_base_url()
    url = f"{base}/mcp/tools"

    payload = {
        "name": "agent_observe",
        "arguments": {
            "sessionId": session_id,
            "project": AGENTMEMORY_PROJECT or "unknown",
            "cwd": AGENTMEMORY_CWD or os.getcwd(),
            "text": text,
            "type": obs_type
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers(),
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def get_prompt_from_stdin():
    """Read prompt from stdin or environment."""
    try:
        data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
        return data.get('prompt') or data.get('userPrompt') or ''
    except:
        return ''

def main():
    # Get session info
    session_id = AGENTMEMORY_SESSION_ID or 'unknown'
    prompt = get_prompt_from_stdin()

    # Don't log empty prompts
    if not prompt.strip():
        print(json.dumps({"skipped": "empty prompt"}))
        return

    # Send observation
    result = send_observation(
        session_id,
        f"User prompt: {prompt[:500]}",  # Truncate long prompts
        "conversation"
    )

    print(json.dumps({
        "session_id": session_id,
        "prompt_length": len(prompt),
        "result": result
    }))

if __name__ == "__main__":
    main()
