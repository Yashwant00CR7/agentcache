#!/usr/bin/env python
import sys
import os
import json
from hook_utils import resolve_project, is_sdk_child, api_call

def main():
    inject_context = os.environ.get("AGENTMEMORY_INJECT_CONTEXT") == "true"
    if not inject_context:
        return

    try:
        input_data = sys.stdin.read()
        if not input_data:
            return
        data = json.loads(input_data)
    except Exception:
        return

    if is_sdk_child(data):
        return

    tool_name = data.get("tool_name") or data.get("toolName")
    if not isinstance(tool_name, str):
        return

    normalized_tool = tool_name.lower()
    file_tools = ["edit", "write", "create", "read", "view", "glob", "grep"]
    if normalized_tool not in file_tools:
        return

    raw_input = data.get("tool_input") or data.get("toolArgs") or {}
    tool_input = raw_input if isinstance(raw_input, dict) else {}

    files = []
    file_keys = ["path", "file"] if normalized_tool == "grep" else ["file_path", "path", "file", "pattern"]
    for key in file_keys:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            files.append(val.strip())

    if not files:
        return

    terms = []
    if normalized_tool in ["grep", "glob"]:
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str) and pattern.strip():
            terms.append(pattern.strip())

    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    project = data.get("project") or resolve_project(data.get("cwd"))

    payload = {
        "sessionId": session_id,
        "files": files,
        "terms": terms,
        "toolName": tool_name,
        "project": project
    }

    result = api_call("enrich", payload, timeout=2.0)
    if result and result.get("context"):
        sys.stdout.write(result["context"])

if __name__ == "__main__":
    main()
