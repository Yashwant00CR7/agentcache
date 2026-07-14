import os
import re
import json
import hashlib
import datetime
from typing import List, Dict, Any, Tuple

# Constants
MAX_FILES_DEFAULT = 200
MAX_FILES_UPPER_BOUND = 1000

SENSITIVE_PATH_PATTERNS = [
    re.compile(r"(^|[\\/_.-])secret([\\/_.-]|s?$)", re.IGNORECASE),
    re.compile(r"(^|[\\/_.-])credentials?([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/_.-])private[_-]?key([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.env(\.[\w-]+)?$", re.IGNORECASE),
    re.compile(r"(^|[\\/_.-])id_rsa([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])auth[_-]?token([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])bearer[_-]?token([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])access[_-]?token([\\/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])api[_-]?token([\\/_.-]|$)", re.IGNORECASE),
]

LESSON_PATTERNS = [
    re.compile(
        r"\b(always|never|don'?t|do not|make sure|remember to|note:|caveat:|warning:)\b[^.\n]{10,200}[.!\n]",
        re.IGNORECASE,
    ),
    re.compile(r"\b(prefer|avoid)\s[^.\n]{10,200}[.!\n]", re.IGNORECASE),
]


def generate_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"


def fingerprint_id(prefix: str, content: str) -> str:
    # Hash content for stable ID (similar to TS fingerprintId)
    h = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    return f"{prefix}_{h[:32]}"


def is_sensitive(path: str) -> bool:
    return any(pattern.search(path) for pattern in SENSITIVE_PATH_PATTERNS)


def derive_project(cwd: str) -> str:
    if not cwd:
        return "unknown"
    parts = [p for p in re.split(r"[\\/]", cwd) if p]
    return parts[-1] if parts else "unknown"


def to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    return "\n".join(parts)


def extract_tool_uses(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            out.append(
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", "unknown"),
                    "input": item.get("input"),
                }
            )
    return out


def extract_tool_results(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_result":
            out.append(
                {
                    "toolUseId": item.get("tool_use_id", ""),
                    "output": item.get("content"),
                    "isError": item.get("is_error") is True,
                }
            )
    return out


def parse_jsonl_text(text: str, fallback_session_id: str = None) -> Dict[str, Any]:
    lines = [line_str for line_str in text.split("\n") if line_str.strip()]
    entries = []
    for line in lines:
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
        except Exception:
            pass

    session_id = ""
    cwd = ""
    first_ts = ""
    last_ts = ""
    observations = []

    for entry in entries:
        if entry.get("sessionId") and not session_id:
            session_id = entry["sessionId"]
        if entry.get("cwd") and not cwd:
            cwd = entry["cwd"]

        ts = entry.get("timestamp") or datetime.datetime.utcnow().isoformat() + "Z"
        if not first_ts:
            first_ts = ts
        last_ts = ts

        msg = entry.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")

        if entry.get("type") == "user" and role == "user":
            tool_results = extract_tool_results(content)
            if tool_results:
                for result in tool_results:
                    observations.append(
                        {
                            "id": generate_id("obs"),
                            "sessionId": session_id or "imported",
                            "timestamp": ts,
                            "hookType": "post_tool_failure"
                            if result["isError"]
                            else "post_tool_use",
                            "toolName": None,
                            "toolInput": {"toolUseId": result["toolUseId"]},
                            "toolOutput": result["output"],
                            "raw": entry,
                        }
                    )
            else:
                txt = to_text(content)
                if txt.strip():
                    observations.append(
                        {
                            "id": generate_id("obs"),
                            "sessionId": session_id or "imported",
                            "timestamp": ts,
                            "hookType": "prompt_submit",
                            "userPrompt": txt,
                            "raw": entry,
                        }
                    )
        elif entry.get("type") == "assistant" and role == "assistant":
            txt = to_text(content)
            tools = extract_tool_uses(content)
            if txt.strip():
                observations.append(
                    {
                        "id": generate_id("obs"),
                        "sessionId": session_id or "imported",
                        "timestamp": ts,
                        "hookType": "stop",
                        "assistantResponse": txt,
                        "raw": entry,
                    }
                )
            for tool in tools:
                observations.append(
                    {
                        "id": generate_id("obs"),
                        "sessionId": session_id or "imported",
                        "timestamp": ts,
                        "hookType": "pre_tool_use",
                        "toolName": tool["name"],
                        "toolInput": tool["input"],
                        "raw": {"toolUseId": tool["id"], "entry": entry},
                    }
                )

    effective_session_id = session_id or fallback_session_id or generate_id("sess")
    for obs in observations:
        if obs["sessionId"] == "imported":
            obs["sessionId"] = effective_session_id

    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    return {
        "sessionId": effective_session_id,
        "project": derive_project(cwd),
        "cwd": cwd or os.getcwd(),
        "startedAt": first_ts or now_iso,
        "endedAt": last_ts or now_iso,
        "observations": observations,
    }


def derive_crystal_and_lessons(
    kv,
    session_id: str,
    project: str,
    raw_obs: List[Dict[str, Any]],
    compressed: List[Dict[str, Any]],
    first_prompt: str = None,
) -> None:
    from functions import KV

    if not raw_obs:
        return
    created_at = datetime.datetime.utcnow().isoformat() + "Z"

    files = set()
    tools = set()
    for c in compressed:
        for f in c.get("files", []):
            files.add(f)
        if c.get("type") and c.get("type") != "conversation" and c.get("title"):
            tools.add(c["title"])

    assistant_texts = []
    user_prompts = []
    for r in raw_obs:
        if (
            isinstance(r.get("assistantResponse"), str)
            and r["assistantResponse"].strip()
        ):
            assistant_texts.append(r["assistantResponse"])
        if isinstance(r.get("userPrompt"), str) and r["userPrompt"].strip():
            user_prompts.append(r["userPrompt"])

    lesson_matches = {}
    for text in (assistant_texts + user_prompts)[:200]:
        for pat in LESSON_PATTERNS:
            for m in pat.finditer(text):
                if len(lesson_matches) >= 40:
                    break
                snippet = re.sub(r"\s+", " ", m.group(0)).strip()
                if 20 <= len(snippet) <= 220:
                    key = snippet.lower()
                    if key not in lesson_matches:
                        lesson_matches[key] = snippet

    lesson_entries = list(lesson_matches.values())[:20]
    lesson_ids = []
    for content in lesson_entries:
        lesson_id = fingerprint_id("lesson", content.lower())
        try:
            existing = kv.get(KV.lessons, lesson_id)
            if existing:
                existing_sources = existing.get("sourceIds", [])
                merged_sources = (
                    existing_sources
                    if session_id in existing_sources
                    else (existing_sources + [session_id])
                )
                existing_tags = existing.get("tags", [])
                merged_tags = (
                    existing_tags
                    if "auto-import" in existing_tags
                    else (existing_tags + ["auto-import"])
                )

                existing["sourceIds"] = merged_sources
                existing["tags"] = merged_tags
                existing["reinforcements"] = existing.get("reinforcements", 0) + 1
                existing["updatedAt"] = created_at
                existing["lastReinforcedAt"] = created_at
                kv.set(KV.lessons, lesson_id, existing)
            else:
                lesson = {
                    "id": lesson_id,
                    "content": content,
                    "context": first_prompt or project,
                    "confidence": 0.4,
                    "reinforcements": 0,
                    "source": "consolidation",
                    "sourceIds": [session_id],
                    "project": project,
                    "tags": ["auto-import"],
                    "createdAt": created_at,
                    "updatedAt": created_at,
                    "decayRate": 0.05,
                }
                kv.set(KV.lessons, lesson_id, lesson)
            lesson_ids.append(lesson_id)
        except Exception:
            pass

    crystal_id = fingerprint_id("crystal", session_id)
    if first_prompt:
        narrative_preview = first_prompt[:300]
    else:
        previews = []
        for c in compressed[:5]:
            p = c.get("narrative") or c.get("title")
            if p:
                previews.append(p)
        narrative_preview = (" · ".join(previews))[:300]

    try:
        existing_crystal = kv.get(KV.crystals, crystal_id) or {}
        crystal = {
            "id": crystal_id,
            "narrative": narrative_preview
            or f"Session {session_id[:12]} ({len(raw_obs)} observations)",
            "keyOutcomes": list(tools)[:8],
            "filesAffected": list(files)[:20],
            "lessons": lesson_ids,
            "sourceActionIds": existing_crystal.get("sourceActionIds", []),
            "sessionId": session_id,
            "project": project,
            "createdAt": existing_crystal.get("createdAt", created_at),
        }
        kv.set(KV.crystals, crystal_id, crystal)
    except Exception:
        pass


def find_jsonl_files(root: str, limit=200) -> Tuple[List[str], bool, int, bool]:
    out = []
    discovered = 0
    walked = 0
    traversal_cap = max(limit * 50, 50000)

    for dirpath, dirnames, filenames in os.walk(root):
        if walked >= traversal_cap:
            break

        # skip symlinks or hidden directories
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d))
        ]

        for name in filenames:
            walked += 1
            if walked >= traversal_cap:
                break
            if name.endswith(".jsonl"):
                full = os.path.join(dirpath, name)
                if not os.path.islink(full):
                    discovered += 1
                    if len(out) < limit:
                        out.append(full)

    traversal_capped = walked >= traversal_cap
    truncated = discovered > len(out) or traversal_capped
    return out, truncated, discovered, traversal_capped


def import_jsonl_data(kv, path: str = None, max_files: int = None) -> Dict[str, Any]:
    from functions import KV, build_synthetic_compression, _bm25_index

    default_root = os.path.expanduser(os.path.join("~", ".claude", "projects"))
    raw_path = path or default_root

    expanded = os.path.expanduser(raw_path)
    abs_path = os.path.abspath(expanded)

    if is_sensitive(abs_path):
        return {"success": False, "error": "refusing to process sensitive-looking path"}
    if os.path.islink(abs_path):
        return {"success": False, "error": "symlinks are not supported"}
    if not os.path.exists(abs_path):
        return {"success": False, "error": "path not found"}

    limit = (
        max_files if isinstance(max_files, int) and max_files > 0 else MAX_FILES_DEFAULT
    )
    limit = min(limit, MAX_FILES_UPPER_BOUND)

    files = []
    truncated = False
    discovered = 0
    traversal_capped = False

    if os.path.isdir(abs_path):
        files, truncated, discovered, traversal_capped = find_jsonl_files(
            abs_path, limit
        )
    elif os.path.isfile(abs_path) and abs_path.endswith(".jsonl"):
        files = [abs_path]
        discovered = 1
    else:
        return {"success": False, "error": "path must be a .jsonl file or directory"}

    if not files:
        return {
            "success": True,
            "imported": 0,
            "sessionIds": [],
            "observations": 0,
            "discovered": discovered,
            "truncated": truncated,
            "traversalCapped": traversal_capped,
            "maxFiles": limit,
            "maxFilesUpperBound": MAX_FILES_UPPER_BOUND,
        }

    session_ids = []
    observation_count = 0

    for file in files:
        if is_sensitive(file):
            continue
        if os.path.islink(file):
            continue

        try:
            with open(file, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            print(f"[import-jsonl] Failed to read {file}: {e}")
            continue

        parsed = parse_jsonl_text(text, generate_id("sess"))
        if not parsed["observations"]:
            continue

        first_prompt_obs = None
        for o in parsed["observations"]:
            if isinstance(o.get("userPrompt"), str) and o["userPrompt"].strip():
                first_prompt_obs = o
                break

        first_prompt = None
        if first_prompt_obs:
            first_prompt = re.sub(r"\s+", " ", first_prompt_obs["userPrompt"]).strip()[
                :200
            ]

        existing = kv.get(KV.sessions, parsed["sessionId"])
        if existing:
            existing["observationCount"] = existing.get("observationCount", 0) + len(
                parsed["observations"]
            )
            if parsed["endedAt"] > existing.get("endedAt", ""):
                existing["endedAt"] = parsed["endedAt"]
            if existing.get("status") == "active":
                existing["status"] = "completed"

            existing_tags = existing.get("tags", [])
            if "jsonl-import" not in existing_tags:
                existing["tags"] = existing_tags + ["jsonl-import"]
            if not existing.get("firstPrompt") and first_prompt:
                existing["firstPrompt"] = first_prompt
            if not existing.get("id"):
                existing["id"] = parsed["sessionId"]

            kv.set(KV.sessions, parsed["sessionId"], existing)
        else:
            session = {
                "id": parsed["sessionId"],
                "project": parsed["project"],
                "cwd": parsed["cwd"],
                "startedAt": parsed["startedAt"],
                "endedAt": parsed["endedAt"],
                "status": "completed",
                "observationCount": len(parsed["observations"]),
                "tags": ["jsonl-import"],
                "firstPrompt": first_prompt,
            }
            kv.set(KV.sessions, session["id"], session)

        from functions import vector_index_add_guarded

        compressed = []
        for obs in parsed["observations"]:
            synthetic = build_synthetic_compression(obs)
            compressed.append(synthetic)
            kv.set(KV.observations(parsed["sessionId"]), obs["id"], synthetic)

            # Index
            _bm25_index.add(synthetic)
            comb_text = synthetic["title"] + " " + (synthetic.get("narrative") or "")
            vector_index_add_guarded(
                synthetic["id"],
                synthetic["sessionId"],
                comb_text,
                {"kind": "synthetic", "logId": synthetic["id"]},
            )

        observation_count += len(parsed["observations"])
        session_ids.append(parsed["sessionId"])

        derive_crystal_and_lessons(
            kv,
            parsed["sessionId"],
            parsed["project"],
            parsed["observations"],
            compressed,
            first_prompt,
        )

    # Save the updated persistence state
    import functions

    if functions._index_persistence:
        try:
            functions._index_persistence.save()
        except Exception as e:
            print(f"[import-jsonl] Warning saving index persistence: {e}")

    # Audit trail
    try:
        from functions import log_audit

        log_audit(
            kv,
            "import",
            "mem::replay::import-jsonl",
            f"Imported {len(session_ids)} sessions: {','.join(session_ids[:3])}...",
        )
    except Exception:
        pass

    # Dolt commit if enabled
    try:
        kv.commit_version(
            f"Import {len(session_ids)} Claude Code sessions from JSONL", "system"
        )
    except Exception:
        pass

    return {
        "success": True,
        "imported": len(files),
        "sessionIds": session_ids,
        "observations": observation_count,
        "discovered": discovered,
        "truncated": truncated,
        "traversalCapped": traversal_capped,
        "maxFiles": limit,
        "maxFilesUpperBound": MAX_FILES_UPPER_BOUND,
    }


def kind_from_hook(obs: Dict[str, Any]) -> str:
    ht = obs.get("hookType")
    if ht == "session_start":
        return "session_start"
    elif ht == "session_end":
        return "session_end"
    elif ht == "prompt_submit":
        return "prompt"
    elif ht == "stop":
        return "response" if obs.get("assistantResponse") else "hook"
    elif ht == "pre_tool_use":
        return "tool_call"
    elif ht == "post_tool_use":
        return "tool_result"
    elif ht == "post_tool_failure":
        return "tool_error"
    else:
        return "hook"


def label_for(obs: Dict[str, Any], kind: str) -> str:
    if kind == "prompt":
        val = obs.get("userPrompt") or "User prompt"
        return val[:79] + "…" if len(val) > 80 else val
    elif kind == "response":
        val = obs.get("assistantResponse") or "Assistant response"
        return val[:79] + "…" if len(val) > 80 else val
    elif kind == "tool_call":
        return f"{obs.get('toolName') or 'tool'} ▸ call"
    elif kind == "tool_result":
        return f"{obs.get('toolName') or 'tool'} ▸ result"
    elif kind == "tool_error":
        return f"{obs.get('toolName') or 'tool'} ▸ error"
    elif kind == "session_start":
        return "Session start"
    elif kind == "session_end":
        return "Session end"
    else:
        return obs.get("hookType") or ""


def estimate_duration_ms(event: Dict[str, Any]) -> int:
    body = event.get("body") or ""
    tool_input = event.get("toolInput") or ""
    tool_output = event.get("toolOutput") or ""

    chars = len(body)
    if isinstance(tool_input, str):
        chars += len(tool_input)
    elif tool_input is not None:
        chars += len(json.dumps(tool_input))

    if isinstance(tool_output, str):
        chars += len(tool_output)
    elif tool_output is not None:
        chars += len(json.dumps(tool_output))

    if chars == 0:
        return 300
    ms = round((chars / 40) * 1000)
    return max(300, min(20000, ms))


def project_timeline(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not observations:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return {
            "sessionId": "",
            "startedAt": now,
            "endedAt": now,
            "totalDurationMs": 0,
            "eventCount": 0,
            "events": [],
        }

    sorted_obs = sorted(observations, key=lambda o: o.get("timestamp", ""))
    started_at = sorted_obs[0].get("timestamp", "")

    try:
        import dateutil.parser

        start_dt = dateutil.parser.isoparse(started_at)
        start_ms = start_dt.timestamp() * 1000
    except Exception:
        start_ms = 0

    events = []
    synthetic_offset = 0
    all_same_ts = all(o.get("timestamp") == started_at for o in sorted_obs)

    for obs in sorted_obs:
        kind = kind_from_hook(obs)
        body = (
            obs.get("userPrompt")
            if kind == "prompt"
            else (obs.get("assistantResponse") if kind == "response" else None)
        )

        try:
            import dateutil.parser

            obs_dt = dateutil.parser.isoparse(obs.get("timestamp", ""))
            obs_ms = obs_dt.timestamp() * 1000
            offset_ms = (
                int(max(0, obs_ms - start_ms)) if not all_same_ts else synthetic_offset
            )
        except Exception:
            offset_ms = synthetic_offset

        event = {
            "id": obs.get("id"),
            "sessionId": obs.get("sessionId"),
            "ts": obs.get("timestamp"),
            "offsetMs": offset_ms,
            "durationMs": 0,
            "kind": kind,
            "label": label_for(obs, kind),
            "body": body,
            "toolName": obs.get("toolName"),
            "toolInput": obs.get("toolInput"),
            "toolOutput": obs.get("toolOutput"),
        }
        event["durationMs"] = estimate_duration_ms(event)
        events.append(event)
        synthetic_offset += event["durationMs"]

    if not events:
        total_duration_ms = 0
    else:
        last = events[-1]
        total_duration_ms = last["offsetMs"] + last["durationMs"]

    return {
        "sessionId": sorted_obs[0].get("sessionId"),
        "startedAt": started_at,
        "endedAt": sorted_obs[-1].get("timestamp"),
        "totalDurationMs": total_duration_ms,
        "eventCount": len(events),
        "events": events,
    }
