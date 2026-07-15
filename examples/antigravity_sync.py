#!/usr/bin/env python3
"""
Antigravity transcript sync — personal/business-specific sync logic
extracted from the generic MCP bridge (D1.3).

Reads Gemini Antigravity brain transcripts and posts them to agentmemory
as legacy session observations.

Usage (standalone):
  python examples/antigravity_sync.py [--mode current_session|current_folder|all]

Or via MCP if you re-add these functions to your own mcp_stdio.py.
"""

import os
import sys
import json
import glob
import re
import datetime
import requests

# Locate the base URL from the environment
BASE = os.getenv("AGENTMEMORY_URL", "http://127.0.0.1:3111").rstrip("/")
if not BASE.endswith("/agentmemory"):
    BASE = f"{BASE}/agentmemory"

_secret = os.getenv("AGENTMEMORY_SECRET")


def _headers():
    h = {"Content-Type": "application/json"}
    if _secret:
        h["Authorization"] = f"Bearer {_secret}"
    return h


def perform_antigravity_sync_local(args):
    """Sync Antigravity transcripts → agentmemory legacy session observations."""
    mode = args.get("mode") or "current_session"
    current_conversation_id = args.get("currentConversationId")
    current_folder = args.get("currentFolder")

    brain_dir = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain")
    if not os.path.exists(brain_dir):
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": False,
                "syncedSessions": [],
                "observationsAdded": 0,
                "error": f"Brain directory not found at {brain_dir}"
            })}]
        }

    pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
    files = glob.glob(pattern)
    if not files:
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": True,
                "syncedSessions": [],
                "observationsAdded": 0
            })}]
        }

    conversations = []
    for fpath in files:
        try:
            mtime = os.path.getmtime(fpath)
            convo_id = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(fpath))))
            conversations.append({"id": convo_id, "transcriptPath": fpath, "mtime": mtime})
        except Exception:
            pass

    if not conversations:
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "syncedSessions": [], "observationsAdded": 0})}]}

    conversations.sort(key=lambda x: x["mtime"], reverse=True)

    targets = []
    if mode == "current_session":
        if current_conversation_id:
            match = next((c for c in conversations if c["id"] == current_conversation_id), None)
            if match:
                targets = [match]
        else:
            targets = [conversations[0]]
    elif mode == "current_folder":
        target_folder = (current_folder or os.getcwd()).replace("\\", "/").lower().strip()
        for convo in conversations:
            try:
                with open(convo["transcriptPath"], "r", encoding="utf-8") as tf:
                    text = tf.read().lower().replace("\\/", "/").replace("\\\\", "/")
                    if target_folder in text:
                        targets.append(convo)
            except Exception:
                pass
    elif mode == "all":
        targets = conversations
    else:
        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": f"Invalid mode: {mode}"})}]}

    if not targets:
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "syncedSessions": [], "observationsAdded": 0})}]}

    synced_sessions = []
    observations_added = 0
    headers_dict = _headers()

    for convo in targets:
        convo_id = convo["id"]
        tpath = convo["transcriptPath"]
        session_id = f"antigravity_{convo_id[:18].replace('-', '_')}"

        project_path = None
        try:
            with open(tpath, "r", encoding="utf-8") as tf:
                first_line = tf.readline()
                if first_line:
                    step = json.loads(first_line)
                    m = re.search(r"\[([^\]]+)\]\s*->\s*\[([^\]]+)\]", step.get("content", ""))
                    if m:
                        project_path = m.group(2)
        except Exception:
            pass
        if not project_path:
            project_path = os.getcwd()

        turns = []
        current_prompt = None
        current_timestamp = None
        try:
            with open(tpath, "r", encoding="utf-8") as tf:
                for line in tf:
                    if not line.strip():
                        continue
                    try:
                        step = json.loads(line)
                        step_type = step.get("type")
                        if step_type == "USER_INPUT":
                            p_text = step.get("content", "")
                            if "<USER_REQUEST>" in p_text:
                                p_text = p_text.split("<USER_REQUEST>")[1]
                            if "</USER_REQUEST>" in p_text:
                                p_text = p_text.split("</USER_REQUEST>")[0]
                            current_prompt = p_text.strip()
                            current_timestamp = step.get("created_at")
                        elif step_type == "PLANNER_RESPONSE" and current_prompt:
                            ts = current_timestamp or step.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                            turns.append({"prompt": current_prompt, "response": step.get("content", ""), "timestamp": ts})
                            current_prompt = None
                            current_timestamp = None
                    except Exception:
                        pass
        except Exception:
            continue

        if not turns:
            continue

        convo_synced = False
        for turn in turns:
            payload = {
                "sessionId": session_id,
                "project": project_path,
                "cwd": project_path,
                "hookType": "post_tool_use",
                "timestamp": turn["timestamp"],
                "agentId": "antigravity",
                "data": {"tool_name": "conversation", "tool_input": turn["prompt"], "tool_output": turn["response"]},
            }
            try:
                requests.post(f"{BASE}/observe", headers=headers_dict, json=payload, timeout=10)
                observations_added += 1
                convo_synced = True
            except Exception:
                pass

        if convo_synced:
            synced_sessions.append(convo_id)

    return {"content": [{"type": "text", "text": json.dumps({
        "success": True,
        "syncedSessions": synced_sessions,
        "observationsAdded": observations_added,
    })}]}


def perform_antigravity_sync_all_local(args):
    """Master sync: transcript + crystallize + reflect."""
    sync_res_outer = perform_antigravity_sync_local(args)
    try:
        sync_res = json.loads(sync_res_outer["content"][0]["text"])
    except Exception:
        return sync_res_outer

    if not sync_res.get("success"):
        return sync_res_outer

    processed_sessions = sync_res.get("syncedSessions", [])
    crystallizations = {}
    reflections = {}
    headers_dict = _headers()

    for cid in processed_sessions:
        session_id = f"antigravity_{cid[:18].replace('-', '_')}"
        try:
            r_sum = requests.post(f"{BASE}/summarize", headers=headers_dict, json={"sessionId": session_id}, timeout=30)
            crystallizations[session_id] = r_sum.json() if r_sum.status_code == 200 else {"success": False, "error": r_sum.text}
        except Exception as e:
            crystallizations[session_id] = {"success": False, "error": str(e)}

        try:
            r_ref = requests.post(f"{BASE}/slot/reflect", headers=headers_dict, json={"sessionId": session_id, "maxObservations": 50}, timeout=30)
            reflections[session_id] = r_ref.json() if r_ref.status_code == 200 else {"success": False, "error": r_ref.text}
        except Exception as e:
            reflections[session_id] = {"success": False, "error": str(e)}

    return {"content": [{"type": "text", "text": json.dumps({
        "success": True,
        "syncedSessions": processed_sessions,
        "observationsAdded": sync_res.get("observationsAdded", 0),
        "crystallizations": crystallizations,
        "reflections": reflections,
    }, indent=2)}]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync Antigravity transcripts to agentmemory")
    parser.add_argument("--mode", default="current_session", choices=["current_session", "current_folder", "all"])
    parser.add_argument("--sync-all", action="store_true", help="Also crystallize and reflect")
    a = parser.parse_args()

    if a.sync_all:
        result = perform_antigravity_sync_all_local({"mode": a.mode})
    else:
        result = perform_antigravity_sync_local({"mode": a.mode})

    print(result["content"][0]["text"])
