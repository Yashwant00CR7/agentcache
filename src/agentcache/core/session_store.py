"""Session store — observe, list/get/create/end sessions, timeline, folder operations."""

import datetime
import json
import os
from typing import Any, Dict, List, Optional

from ..db import StateKV
from ..storage.paths import generate_id
from .config import commit_if_enabled, get_agent_id
from .image_store import extract_image, save_image_to_disk
from .infer import build_synthetic_compression, vector_index_add_guarded
from .kv_scopes import KV
from .privacy import strip_private_data


def auto_complete_old_active_sessions(
    kv: StateKV,
    current_session_id: str,
    project: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> int:
    sessions = kv.list(KV.sessions)
    count = 0
    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    for s in sessions:
        if s.get("id") != current_session_id and s.get("status") == "active":
            if project and s.get("project") != project:
                continue
            if agent_id and s.get("agentId") != agent_id:
                continue
            s["status"] = "completed"
            if "endedAt" not in s:
                s["endedAt"] = now
            s["updatedAt"] = now
            kv.set(KV.sessions, s["id"], s)
            count += 1
    if count > 0:
        print(f"[session] Auto-completed {count} dangling active sessions.")
    return count


def _get_observation_store(kv: StateKV):
    from .. import app as app_module
    from .. import legacy as _legacy

    if getattr(app_module, "observation_store", None) is not None:
        return app_module.observation_store
    from .observation_store import ObservationStore

    return ObservationStore(kv, search_service=_legacy._search_service)


def observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    from .. import legacy as _legacy

    session_id = payload.get("sessionId")
    hook_type = payload.get("hookType")
    timestamp = payload.get("timestamp")

    if not session_id or not hook_type or not timestamp:
        raise ValueError(
            "Invalid payload: sessionId, hookType, and timestamp are required"
        )

    obs_id = generate_id("obs")
    sanitized_data = payload.get("data")
    try:
        json_str = json.dumps(payload.get("data"))
        sanitized = strip_private_data(json_str)
        sanitized_data = json.loads(sanitized)
    except Exception:
        sanitized_data = strip_private_data(str(payload.get("data")))

    raw = {
        "id": obs_id,
        "sessionId": session_id,
        "timestamp": timestamp,
        "hookType": hook_type,
        "raw": sanitized_data,
    }

    extracted_img = extract_image(sanitized_data)
    if isinstance(sanitized_data, dict):
        if hook_type in ("post_tool_use", "post_tool_failure"):
            raw["toolName"] = sanitized_data.get("tool_name")
            raw["toolInput"] = sanitized_data.get("tool_input")
            raw["toolOutput"] = sanitized_data.get("tool_output") or sanitized_data.get(
                "error"
            )
        if hook_type == "prompt_submit":
            raw["userPrompt"] = sanitized_data.get("prompt")
        if extracted_img:
            raw["modality"] = (
                "mixed"
                if (
                    raw.get("toolInput")
                    or raw.get("toolOutput")
                    or raw.get("userPrompt")
                )
                else "image"
            )
    elif isinstance(sanitized_data, str) and extracted_img:
        raw["modality"] = "image"

    max_obs = int(os.getenv("MAX_OBS_PER_SESSION", "500"))
    if max_obs > 0:
        existing = kv.list(KV.observations(session_id))
        actual_obs_count = sum(
            1 for o in existing if not str(o.get("id", "")).endswith(":raw")
        )
        if actual_obs_count >= max_obs:
            raise ValueError(f"Session observation limit reached ({max_obs})")

    existing_session = kv.get(KV.sessions, session_id)
    inherited_agent_id = (
        existing_session.get("agentId") if existing_session else get_agent_id()
    )
    if inherited_agent_id:
        raw["agentId"] = inherited_agent_id

    if extracted_img and (
        extracted_img.startswith("data:image/")
        or extracted_img.startswith("iVBORw0KGgo")
        or extracted_img.startswith("/9j/")
    ):
        try:
            file_path, bytes_written = save_image_to_disk(extracted_img)
            raw["imageData"] = file_path

            img_refs = kv.get(KV.imageRefs, file_path) or 0
            kv.set(KV.imageRefs, file_path, img_refs + 1)
        except Exception as ex:
            print(f"[image store] failed: {ex}")

    raw["id"] = f"{obs_id}:raw"
    kv.set(KV.observations(session_id), raw["id"], raw)

    _legacy.broadcast_stream(
        {
            "type": "raw_observation",
            "sessionId": session_id,
            "data": {"type": "raw", "observation": raw, "sessionId": session_id},
        }
    )

    if existing_session:
        updates = [
            {
                "type": "set",
                "path": "updatedAt",
                "value": datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {
                "type": "set",
                "path": "observationCount",
                "value": (existing_session.get("observationCount") or 0) + 1,
            },
        ]
        if not existing_session.get("firstPrompt") and isinstance(
            raw.get("userPrompt"), str
        ):
            trimmed = " ".join(raw["userPrompt"].split()).strip()
            if trimmed:
                updates.append(
                    {"type": "set", "path": "firstPrompt", "value": trimmed[:200]}
                )
        kv.update(KV.sessions, session_id, updates)
    else:
        project = payload.get("project") or "unknown"
        auto_complete_old_active_sessions(
            kv, session_id, project=project, agent_id=inherited_agent_id
        )
        cwd = payload.get("cwd") or os.getcwd()
        trimmed_prompt = None
        if isinstance(raw.get("userPrompt"), str):
            trimmed_prompt = " ".join(raw["userPrompt"].split()).strip()[:200]
        ts = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        new_sess = {
            "id": session_id,
            "project": project,
            "cwd": cwd,
            "startedAt": payload.get("timestamp") or ts,
            "updatedAt": ts,
            "status": "active",
            "observationCount": 1,
        }
        if inherited_agent_id:
            new_sess["agentId"] = inherited_agent_id
        if trimmed_prompt:
            new_sess["firstPrompt"] = trimmed_prompt
        kv.set(KV.sessions, session_id, new_sess)

    raw_for_synthetic = dict(raw)
    raw_for_synthetic["id"] = obs_id
    synthetic = build_synthetic_compression(raw_for_synthetic)
    for k in ["hookType", "raw", "toolName", "toolInput", "toolOutput", "userPrompt"]:
        if k in raw_for_synthetic:
            synthetic[k] = raw_for_synthetic[k]
    kv.set(KV.observations(session_id), obs_id, synthetic)
    if _legacy._search_service:
        _legacy._search_service.bm25.add(synthetic)

    comb_text = synthetic["title"] + " " + (synthetic.get("narrative") or "")
    vector_index_add_guarded(
        synthetic["id"],
        synthetic["sessionId"],
        comb_text,
        {"kind": "synthetic", "logId": synthetic["id"]},
    )

    if _legacy._search_service:
        _legacy._search_service.schedule_persist()

    _legacy.broadcast_stream(
        {
            "type": "compressed_observation",
            "sessionId": session_id,
            "data": {
                "type": "compressed",
                "observation": synthetic,
                "sessionId": session_id,
            },
        }
    )

    commit_if_enabled(
        kv,
        f"Observe: {synthetic.get('title', 'observation')} in session {session_id[:8]}",
        synthetic.get("agentId"),
    )

    return {"observationId": obs_id}


def folder_observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a new observation scoped to a (folder_path, agent_id) pair."""
    store = _get_observation_store(kv)
    return store.ingest(payload)


def dedup_folder_observations(
    kv: StateKV,
    folder_path_raw: Optional[str],
    agent_id_raw: Optional[str],
) -> Dict[str, Any]:
    """Remove duplicate observations from one or all (folder, agent) pairs."""
    store = _get_observation_store(kv)
    return store.dedup(folder_path_raw, agent_id_raw)


def folder_search(
    kv: StateKV,
    query: str,
    limit: int = 20,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search across all folder observations (and global memories) using BM25 + vector hybrid search."""
    from .. import legacy as _legacy

    if not query or not query.strip():
        return []
    if _legacy._search_service is None:
        return []
    return _legacy._search_service.search(
        query=query, limit=limit, folder_path=folder_path, agent_id=agent_id, kv=kv
    )


def folder_timeline(
    kv: StateKV,
    limit: int = 100,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a folder activity feed — observations sorted by timestamp descending."""
    store = _get_observation_store(kv)
    return store.timeline(
        limit=limit,
        folder_path=folder_path,
        agent_id=agent_id,
        before=before,
        after=after,
    )


def list_sessions(kv: StateKV) -> List[Dict[str, Any]]:
    sessions = kv.list(KV.sessions)
    for s in sessions:
        sid = s.get("id")
        if sid:
            summary = kv.get(KV.summaries, sid)
            if summary:
                s["title"] = summary.get("title")
                s["summary"] = summary.get("narrative")
    sessions.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
    return sessions


def get_session(kv: StateKV, session_id: str) -> Optional[Dict[str, Any]]:
    s = kv.get(KV.sessions, session_id)
    if s:
        summary = kv.get(KV.summaries, session_id)
        if summary:
            s["title"] = summary.get("title")
            s["summary"] = summary.get("narrative")
    return s


def create_session(kv: StateKV, session: Dict[str, Any]) -> Dict[str, Any]:
    auto_complete_old_active_sessions(
        kv,
        session["id"],
        project=session.get("project"),
        agent_id=session.get("agentId"),
    )
    kv.set(KV.sessions, session["id"], session)
    return session


def end_session(kv: StateKV, session_id: str) -> bool:
    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    kv.update(
        KV.sessions,
        session_id,
        [
            {"type": "set", "path": "endedAt", "value": now},
            {"type": "set", "path": "status", "value": "completed"},
        ],
    )
    return True


def timeline(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    anchor = data.get("anchor")
    project = data.get("project")
    session_id = data.get("sessionId")
    before = data.get("before") or 10
    after = data.get("after") or 10

    sessions = kv.list(KV.sessions)
    if session_id:
        sessions = [s for s in sessions if s.get("id") == session_id]
    elif project:
        sessions = [s for s in sessions if s.get("project") == project]

    all_obs = []
    for s in sessions:
        all_obs.extend(kv.list(KV.observations(s["id"])))

    all_obs.sort(key=lambda x: x.get("timestamp", ""))

    anchor_idx = -1
    for idx, obs in enumerate(all_obs):
        if obs["id"] == anchor or obs.get("timestamp", "") >= (anchor or ""):
            anchor_idx = idx
            break

    if anchor_idx == -1:
        anchor_idx = len(all_obs) // 2

    start = max(0, anchor_idx - before)
    end = min(len(all_obs), anchor_idx + after + 1)

    return {
        "success": True,
        "observations": all_obs[start:end],
        "anchorIndex": anchor_idx - start,
    }
