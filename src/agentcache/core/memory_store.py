"""Memory store — remember, evolve, forget global memories."""

import datetime
from typing import Any, Dict

from ..db import StateKV
from ..storage.paths import generate_id
from .config import commit_if_enabled, get_agent_id
from .infer import vector_index_add_guarded
from .kv_scopes import KV
from .privacy import strip_private_data


def jaccard_similarity(a: str, b: str) -> float:
    tokens_a = [t for t in a.split() if len(t) > 2]
    tokens_b = [t for t in b.split() if len(t) > 2]
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union


def memory_to_observation(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": memory["id"],
        "sessionId": memory.get("sessionIds", ["memory"])[0]
        if memory.get("sessionIds")
        else "memory",
        "timestamp": memory["createdAt"],
        "type": "decision",
        "title": memory["title"],
        "facts": [memory["content"]],
        "narrative": memory["content"],
        "concepts": memory.get("concepts", []),
        "files": memory.get("files", []),
        "importance": memory.get("strength", 7),
    }


def remember(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    from .. import legacy as _legacy

    content = data.get("content")
    if not content or not content.strip():
        raise ValueError("content is required")
    content = strip_private_data(content)

    concepts = data.get("concepts") or []
    files = data.get("files") or []
    source_obs = data.get("sourceObservationIds") or []
    ttl_days = data.get("ttlDays")
    mem_type = data.get("type") or "fact"
    project = data.get("project")
    if project:
        project = project.strip()

    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    existing_memories = kv.list(KV.memories)
    superseded_id = None
    superseded_version = 1
    superseded_memory = None
    lower_content = content.lower()

    for existing in existing_memories:
        if existing.get("isLatest") is False:
            continue
        if project and existing.get("project") and existing["project"] != project:
            continue
        similarity = jaccard_similarity(
            lower_content, existing.get("content", "").lower()
        )
        if similarity > 0.7:
            superseded_id = existing["id"]
            superseded_version = existing.get("version") or 1
            superseded_memory = existing
            break

    call_agent_id = data.get("agentId") or get_agent_id()
    new_mem = {
        "id": generate_id("mem"),
        "createdAt": now,
        "updatedAt": now,
        "type": mem_type,
        "title": content[:80],
        "content": content,
        "concepts": concepts,
        "files": files,
        "sessionIds": [],
        "strength": 7,
        "version": superseded_version + 1 if superseded_id else 1,
        "parentId": superseded_id,
        "supersedes": [superseded_id] if superseded_id else [],
        "sourceObservationIds": [i for i in source_obs if i],
        "isLatest": True,
    }
    if call_agent_id:
        new_mem["agentId"] = call_agent_id
    if project:
        new_mem["project"] = project

    if ttl_days and isinstance(ttl_days, (int, float)) and ttl_days > 0:
        forget_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=ttl_days
        )
        new_mem["forgetAfter"] = forget_time.isoformat().replace("+00:00", "Z")
    elif "forgetAfter" in data:
        new_mem["forgetAfter"] = data["forgetAfter"]

    if superseded_memory:
        superseded_memory["isLatest"] = False
        kv.set(KV.memories, superseded_memory["id"], superseded_memory)

    kv.set(KV.memories, new_mem["id"], new_mem)

    if _legacy._search_service:
        try:
            _legacy._search_service.bm25.add(memory_to_observation(new_mem))
        except Exception as ex:
            print(f"[bm25] memory add failed: {ex}")

    comb_text = new_mem["title"] + " " + new_mem["content"]
    vector_index_add_guarded(
        new_mem["id"], "memory", comb_text, {"kind": "memory", "logId": new_mem["id"]}
    )

    if _legacy._search_service:
        _legacy._search_service.schedule_persist()

    commit_if_enabled(
        kv, f"Remember: {new_mem.get('title', '')}", new_mem.get("agentId")
    )

    _legacy.broadcast_stream(
        {
            "type": "memory_created",
            "data": new_mem,
        }
    )

    return {"success": True, "memory": new_mem}


def forget(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a global memory, a folder (folder_path+agent_id), or specific observations."""
    from .. import legacy as _legacy
    from .observation_store import ObservationStore

    def _get_store():
        from .. import app as app_module

        if getattr(app_module, "observation_store", None) is not None:
            return app_module.observation_store
        return ObservationStore(kv, search_service=_legacy._search_service)

    store = _get_store()
    return store.forget(data)


def evolve_memory(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    from .. import legacy as _legacy

    mem_id = data["memoryId"]
    new_content = data["newContent"]
    new_title = data.get("newTitle")

    existing = kv.get(KV.memories, mem_id)
    if not existing:
        raise ValueError("Memory not found")

    existing["isLatest"] = False
    kv.set(KV.memories, existing["id"], existing)

    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    new_mem = dict(existing)
    new_mem["id"] = generate_id("mem")
    new_mem["content"] = new_content
    if new_title:
        new_mem["title"] = new_title
    else:
        new_mem["title"] = new_content[:80]
    new_mem["version"] = existing.get("version", 1) + 1
    new_mem["parentId"] = existing["id"]
    new_mem["supersedes"] = [existing["id"]]
    new_mem["createdAt"] = now
    new_mem["updatedAt"] = now
    new_mem["isLatest"] = True

    kv.set(KV.memories, new_mem["id"], new_mem)

    if _legacy._search_service:
        try:
            _legacy._search_service.bm25.add(memory_to_observation(new_mem))
            _legacy._search_service.bm25.remove(existing["id"])
        except Exception:
            pass

    comb_text = new_mem["title"] + " " + new_mem["content"]
    vector_index_add_guarded(
        new_mem["id"], "memory", comb_text, {"kind": "memory", "logId": new_mem["id"]}
    )
    if _legacy._search_service and _legacy._search_service.vector:
        _legacy._search_service.vector.remove(existing["id"])

    if _legacy._search_service:
        _legacy._search_service.schedule_persist()

    agent_id = data.get("agentId") or get_agent_id() or new_mem.get("agentId")
    commit_if_enabled(
        kv,
        f"Evolve memory {new_mem['id']} (v{new_mem['version']}): {new_mem['title']}",
        agent_id,
    )

    return {"success": True, "memory": new_mem}
