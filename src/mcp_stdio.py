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

def perform_antigravity_sync_local(args):
    import os
    import json
    import glob
    import re
    import requests
    
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
            conversations.append({
                "id": convo_id,
                "transcriptPath": fpath,
                "mtime": mtime
            })
        except Exception:
            pass

    if not conversations:
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": True,
                "syncedSessions": [],
                "observationsAdded": 0
            })}]
        }

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
        target_folder = current_folder if current_folder else ""
        if not target_folder:
            target_folder = os.getcwd()
            
        target_folder_norm = target_folder.replace("\\", "/").lower().strip()
        
        for convo in conversations:
            try:
                with open(convo["transcriptPath"], "r", encoding="utf-8") as tf:
                    text = tf.read().lower()
                    text_norm = text.replace("\\/", "/").replace("\\\\", "/")
                    if target_folder_norm in text_norm:
                        targets.append(convo)
            except Exception:
                pass
    elif mode == "all":
        targets = conversations
    else:
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": False,
                "syncedSessions": [],
                "observationsAdded": 0,
                "error": f"Invalid mode: {mode}"
            })}]
        }

    if not targets:
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": True,
                "syncedSessions": [],
                "observationsAdded": 0
            })}]
        }

    synced_sessions = []
    observations_added = 0

    headers_dict = headers()

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
                    match = re.search(r"\[([^\]]+)\]\s*->\s*\[([^\]]+)\]", step.get("content", ""))
                    if match:
                        project_path = match.group(2)
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
                                parts = p_text.split("<USER_REQUEST>")
                                if len(parts) > 1:
                                    p_text = parts[1]
                            if "</USER_REQUEST>" in p_text:
                                p_text = p_text.split("</USER_REQUEST>")[0]
                            current_prompt = p_text.strip()
                            current_timestamp = step.get("created_at")
                        elif step_type == "PLANNER_RESPONSE" and current_prompt:
                            ts = current_timestamp or step.get("created_at")
                            if not ts:
                                import datetime
                                ts = datetime.datetime.utcnow().isoformat() + "Z"
                            turns.append({
                                "prompt": current_prompt,
                                "response": step.get("content", ""),
                                "timestamp": ts
                            })
                            current_prompt = None
                            current_timestamp = None
                    except Exception:
                        pass
        except Exception:
            continue

        if not turns:
            continue

        existing_inputs = set()
        
        try:
            r = requests.get(f"{BASE}/observations?sessionId={session_id}", headers=headers_dict, timeout=10)
            if r.status_code == 200:
                obs_list = r.json().get("observations", [])
                for obs in obs_list:
                    tool_input = obs.get("toolInput") or (obs.get("raw") or {}).get("tool_input")
                    if tool_input:
                        existing_inputs.add(tool_input.strip())
        except Exception:
            pass

        try:
            sess_check = requests.get(f"{BASE}/sessions", headers=headers_dict, timeout=5)
            session_exists = False
            if sess_check.status_code == 200:
                sessions_list = sess_check.json().get("sessions", [])
                session_exists = any(s.get("id") == session_id for s in sessions_list)
            
            if not session_exists:
                session_payload = {
                    "sessionId": session_id,
                    "project": project_path,
                    "cwd": project_path,
                    "title": f"Antigravity Pair Programming ({convo_id[:8]})",
                    "agentId": "antigravity"
                }
                requests.post(f"{BASE}/session/start", headers=headers_dict, json=session_payload, timeout=10)
        except Exception:
            pass

        convo_synced = False
        for turn in turns:
            prompt = turn["prompt"]
            if prompt.strip() in existing_inputs:
                continue

            payload = {
                "sessionId": session_id,
                "project": project_path,
                "cwd": project_path,
                "hookType": "post_tool_use",
                "timestamp": turn["timestamp"],
                "agentId": "antigravity",
                "data": {
                    "tool_name": "conversation",
                    "tool_input": prompt,
                    "tool_output": turn["response"],
                }
            }
            try:
                requests.post(f"{BASE}/observe", headers=headers_dict, json=payload, timeout=10)
                observations_added += 1
                convo_synced = True
            except Exception:
                pass

        if convo_synced:
            synced_sessions.append(convo_id)

    return {
        "content": [{"type": "text", "text": json.dumps({
            "success": True,
            "syncedSessions": synced_sessions,
            "observationsAdded": observations_added
        })}]
    }

def perform_antigravity_sync_all_local(args):
    sync_res_outer = perform_antigravity_sync_local(args)
    try:
        sync_res = json.loads(sync_res_outer["content"][0]["text"])
    except Exception as ex:
        return sync_res_outer
        
    if not sync_res.get("success"):
        return sync_res_outer
        
    synced_sessions = sync_res.get("syncedSessions") or []
    crystallizations = {}
    reflections = {}
    
    headers_dict = headers()
    
    for cid in synced_sessions:
        session_id = f"antigravity_{cid[:18].replace('-', '_')}"
        
        try:
            r_sum = requests.post(f"{BASE}/summarize", headers=headers_dict, json={"sessionId": session_id}, timeout=30)
            if r_sum.status_code == 200:
                crystallizations[session_id] = r_sum.json()
            else:
                crystallizations[session_id] = {"success": False, "status_code": r_sum.status_code, "error": r_sum.text}
        except Exception as e:
            crystallizations[session_id] = {"success": False, "error": str(e)}
            
        try:
            r_ref = requests.post(f"{BASE}/slot/reflect", headers=headers_dict, json={"sessionId": session_id, "maxObservations": 50}, timeout=30)
            if r_ref.status_code == 200:
                reflections[session_id] = r_ref.json()
            else:
                reflections[session_id] = {"success": False, "status_code": r_ref.status_code, "error": r_ref.text}
        except Exception as e:
            reflections[session_id] = {"success": False, "error": str(e)}
            
    return {
        "content": [{"type": "text", "text": json.dumps({
            "success": True,
            "syncedSessions": synced_sessions,
            "observationsAdded": sync_res.get("observationsAdded", 0),
            "crystallizations": crystallizations,
            "reflections": reflections
        }, indent=2)}]
    }

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
            if name == "memory_antigravity_sync":
                result = perform_antigravity_sync_local(args)
            elif name == "memory_antigravity_sync_all":
                result = perform_antigravity_sync_all_local(args)
            else:
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
