#!/usr/bin/env python
import sys
import json
import time
from datetime import datetime, timezone
from hook_utils import resolve_project, is_sdk_child, api_call_bg

def is_base64_image(val):
    if not isinstance(val, str):
        return False
    return (
        val.startswith("data:image/") or
        val.startswith("iVBORw0KGgo") or
        val.startswith("/9j/")
    )

def extract_image_data(output):
    if is_base64_image(output):
        return output, "[image data extracted]"

    if isinstance(output, dict):
        image_data = None
        clean = {}
        for k, v in output.items():
            if not image_data and is_base64_image(v):
                image_data = v
                clean[k] = "[image data extracted]"
            else:
                clean[k] = v
        return image_data, clean

    return None, output

def truncate(value, max_len):
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + "\n[...truncated]"
        return value
    if isinstance(value, (dict, list)):
        try:
            serialized = json.dumps(value)
            if len(serialized) > max_len:
                return serialized[:max_len] + "...[truncated]"
            return value
        except Exception:
            return str(value)
    return value

def get_tool_output(data):
    if "tool_response" in data:
        return data["tool_response"]
    if "tool_output" in data:
        return data["tool_output"]
    result = data.get("tool_result") or data.get("toolResult")
    if isinstance(result, dict):
        return result.get("text_result_for_llm") or result.get("textResultForLlm") or result
    return result

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
    tool_name = data.get("tool_name") or data.get("toolName")
    tool_input = data.get("tool_input") or data.get("toolArgs")

    raw_output = get_tool_output(data)
    image_data, clean_output = extract_image_data(raw_output)

    payload = {
        "hookType": "post_tool_use",
        "sessionId": session_id,
        "project": resolve_project(data.get("cwd")),
        "cwd": data.get("cwd") or "",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": truncate(clean_output, 8000)
        }
    }
    if image_data:
        payload["data"]["image_data"] = image_data

    api_call_bg("observe", payload)
    # Allow background request to be flushed
    time.sleep(0.5)

if __name__ == "__main__":
    main()
