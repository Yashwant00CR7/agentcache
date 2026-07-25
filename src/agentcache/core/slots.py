"""Memory slots — pinned context windows with read/append/replace/delete."""

import datetime
import re
from typing import Any, Dict, List, Optional

from ..db import StateKV
from .audit_log import safe_audit
from .config import commit_if_enabled, get_agent_id
from .kv_scopes import KV
from .privacy import strip_private_data

DEFAULT_SLOTS = [
    {
        "label": "persona",
        "content": "",
        "sizeLimit": 1000,
        "description": "How the agent should see itself: role, tone, behavioural guidelines.",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "user_preferences",
        "content": "",
        "sizeLimit": 2000,
        "description": "Coding style, tool preferences, naming conventions, and other habits the user wants preserved across sessions.",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "tool_guidelines",
        "content": "",
        "sizeLimit": 1500,
        "description": "Rules the agent should follow when picking or sequencing tools (e.g. prefer X over Y, never run Z without confirmation).",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "project_context",
        "content": "",
        "sizeLimit": 3000,
        "description": "Architecture decisions, codebase conventions, build/test commands, and cross-cutting constraints for the current project.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "guidance",
        "content": "",
        "sizeLimit": 1500,
        "description": "Active advice for the next session: what to focus on, what to avoid, open risks.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "pending_items",
        "content": "",
        "sizeLimit": 2000,
        "description": "Unfinished work, explicit TODOs, and promises made but not yet delivered.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "session_patterns",
        "content": "",
        "sizeLimit": 1500,
        "description": "Recurring behaviours and common struggles observed across recent sessions.",
        "pinned": False,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "self_notes",
        "content": "",
        "sizeLimit": 1500,
        "description": "Free-form notes the agent keeps for itself: hypotheses, dead ends, things to revisit.",
        "pinned": False,
        "readOnly": False,
        "scope": "project",
    },
]


def get_current_project(kv: StateKV) -> Optional[str]:
    try:
        sessions = kv.list(KV.sessions)
        if not sessions:
            return None
        active_sessions = [s for s in sessions if s.get("status") == "active"]
        if active_sessions:
            active_sessions.sort(key=lambda s: s.get("updatedAt", ""), reverse=True)
            return active_sessions[0].get("project")
        sessions.sort(key=lambda s: s.get("updatedAt", ""), reverse=True)
        return sessions[0].get("project")
    except Exception:
        return None


def project_slots_scope(kv: StateKV, project: Optional[str] = None) -> str:
    if not project:
        project = get_current_project(kv)
    if not project:
        return KV.slots
    return f"mem:slots:{project}"


def seed_defaults(kv: StateKV) -> None:
    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    for tmpl in DEFAULT_SLOTS:
        scope = tmpl["scope"]
        target = KV.globalSlots if scope == "global" else KV.slots
        existing = kv.get(target, tmpl["label"])
        if existing:
            continue
        slot = dict(tmpl)
        slot["createdAt"] = now
        slot["updatedAt"] = now
        kv.set(target, tmpl["label"], slot)


def list_pinned_slots(
    kv: StateKV, project: Optional[str] = None
) -> List[Dict[str, Any]]:
    p_slots = kv.list(project_slots_scope(kv, project))
    g_slots = kv.list(KV.globalSlots)
    merged = {}
    for s in g_slots:
        merged[s["label"]] = s
    for s in p_slots:
        merged[s["label"]] = s
    pinned = [
        s for s in merged.values() if s.get("pinned") and s.get("content", "").strip()
    ]
    pinned.sort(key=lambda s: s["label"])
    return pinned


def render_pinned_context(slots: List[Dict[str, Any]]) -> str:
    if not slots:
        return ""
    lines = ["# agentcache pinned slots", ""]
    for s in slots:
        lines.append(f"## {s['label']}")
        lines.append(s["content"].strip())
        lines.append("")
    return "\n".join(lines)


def slot_list(kv: StateKV, project: Optional[str] = None) -> Dict[str, Any]:
    p_slots = kv.list(project_slots_scope(kv, project))
    g_slots = kv.list(KV.globalSlots)
    merged = {}
    for s in g_slots:
        merged[s["label"]] = s
    for s in p_slots:
        merged[s["label"]] = s
    slots = sorted(list(merged.values()), key=lambda s: s["label"])
    return {"success": True, "slots": slots}


def slot_get(kv: StateKV, label: str, project: Optional[str] = None) -> Dict[str, Any]:
    p_scope = project_slots_scope(kv, project)
    project_s = kv.get(p_scope, label)
    if project_s:
        return {"success": True, "slot": project_s, "scope": "project"}
    global_s = kv.get(KV.globalSlots, label)
    if global_s:
        return {"success": True, "slot": global_s, "scope": "global"}
    return {"success": False, "error": "slot not found"}


def slot_create(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    label = data.get("label")
    if not label or not re.match(r"^[a-z][a-z0-9_]*$", label):
        return {
            "success": False,
            "error": "label required (lowercase, starts with letter, [a-z0-9_])",
        }

    scope = data.get("scope") or "project"
    if scope not in ("project", "global"):
        return {"success": False, "error": "scope must be 'project' or 'global'"}

    limit = data.get("sizeLimit") or 2000
    if not isinstance(limit, int) or limit < 1 or limit > 20000:
        return {
            "success": False,
            "error": "sizeLimit must be an integer between 1 and 20000",
        }

    content = strip_private_data(data.get("content") or "")
    if len(content) > limit:
        return {
            "success": False,
            "error": f"content exceeds sizeLimit ({len(content)} > {limit})",
        }

    description = data.get("description") or ""
    pinned = data.get("pinned", True)
    project = data.get("project")

    target_kv = (
        KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
    )
    existing = kv.get(target_kv, label)
    if existing:
        return {"success": False, "error": f"slot already exists in {scope} scope"}

    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    slot = {
        "label": label,
        "content": content,
        "sizeLimit": limit,
        "description": description,
        "pinned": pinned,
        "readOnly": False,
        "scope": scope,
        "createdAt": now,
        "updatedAt": now,
    }
    kv.set(target_kv, label, slot)
    safe_audit(
        kv,
        "slot_create",
        "mem::slot-create",
        [label],
        {"scope": scope, "sizeLimit": limit, "pinned": pinned},
    )

    agent_id = data.get("agentId") or get_agent_id()
    commit_if_enabled(kv, f"Create slot: {label}", agent_id)

    return {"success": True, "slot": slot}


def slot_append(
    kv: StateKV,
    label: str,
    text: str,
    agent_id: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = (
        KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
    )

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    content = slot.get("content") or ""
    sep = "\n" if content and not content.endswith("\n") else ""
    next_content = content + sep + strip_private_data(text)

    limit = slot.get("sizeLimit") or 2000
    if len(next_content) > limit:
        return {
            "success": False,
            "error": f"append would exceed sizeLimit ({len(next_content)} > {limit})",
            "currentSize": len(content),
            "sizeLimit": limit,
        }

    slot["content"] = next_content
    slot["updatedAt"] = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    kv.set(target_kv, label, slot)

    safe_audit(
        kv,
        "slot_append",
        "mem::slot-append",
        [label],
        {"scope": scope, "added": len(text), "total": len(next_content)},
    )

    commit_if_enabled(kv, f"Append slot: {label}", agent_id or get_agent_id())

    return {"success": True, "slot": slot, "size": len(next_content)}


def slot_replace(
    kv: StateKV,
    label: str,
    content: str,
    agent_id: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = (
        KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
    )

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    content = strip_private_data(content)
    limit = slot.get("sizeLimit") or 2000
    if len(content) > limit:
        return {
            "success": False,
            "error": f"content exceeds sizeLimit ({len(content)} > {limit})",
            "sizeLimit": limit,
        }

    before_len = len(slot.get("content") or "")
    slot["content"] = content
    slot["updatedAt"] = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    kv.set(target_kv, label, slot)

    safe_audit(
        kv,
        "slot_replace",
        "mem::slot-replace",
        [label],
        {"scope": scope, "before": before_len, "after": len(content)},
    )

    commit_if_enabled(kv, f"Replace slot: {label}", agent_id or get_agent_id())

    return {"success": True, "slot": slot, "size": len(content)}


def slot_delete(
    kv: StateKV,
    label: str,
    agent_id: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = (
        KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
    )

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    kv.delete(target_kv, label)
    safe_audit(
        kv,
        "slot_delete",
        "mem::slot-delete",
        [label],
        {"scope": scope, "size": len(slot.get("content") or "")},
    )

    commit_if_enabled(kv, f"Delete slot: {label}", agent_id or get_agent_id())

    return {"success": True}


def slot_reflect(kv: StateKV, session_id: str, max_obs: int = 50) -> Dict[str, Any]:
    session = kv.get(KV.sessions, session_id)
    project = session.get("project") if session else None

    observations = kv.list(KV.observations(session_id))
    if not observations:
        return {"success": True, "applied": 0, "reason": "no observations for session"}

    recent = sorted(observations, key=lambda x: x.get("timestamp", ""), reverse=True)[
        :max_obs
    ]

    pending_lines = []
    pattern_counts = {}
    files = set()

    for obs in recent:
        title = (obs.get("title") or "").lower()
        narrative = (obs.get("narrative") or "").lower()
        if "todo" in narrative or "todo" in title:
            pending_lines.append(f"- {obs.get('title') or obs['id']}")
        if obs.get("type") == "error":
            pattern_counts["errors"] = pattern_counts.get("errors", 0) + 1
        if obs.get("type") == "command_run":
            pattern_counts["commands"] = pattern_counts.get("commands", 0) + 1
        for f in obs.get("files") or []:
            files.add(f)

    applied = 0
    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )

    if pending_lines:
        res = slot_get(kv, "pending_items", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = (
                KV.globalSlots
                if scope == "global"
                else project_slots_scope(kv, project)
            )
            already = set((slot.get("content") or "").split("\n"))
            fresh = [line for line in pending_lines if line not in already]
            if fresh:
                sep = (
                    "\n"
                    if slot.get("content") and not slot["content"].endswith("\n")
                    else ""
                )
                next_content = (slot.get("content") or "") + sep + "\n".join(fresh)
                limit = slot.get("sizeLimit") or 2000
                if len(next_content) > limit:
                    next_content = next_content[-limit:]
                slot["content"] = next_content
                slot["updatedAt"] = now
                kv.set(target_kv, "pending_items", slot)
                applied += 1

    if pattern_counts:
        res = slot_get(kv, "session_patterns", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = (
                KV.globalSlots
                if scope == "global"
                else project_slots_scope(kv, project)
            )
            summary = [f"last reflection: {now}"]
            for k, v in pattern_counts.items():
                summary.append(f"- {k}: {v} in last {len(recent)} observations")
            next_content = "\n".join(summary)
            limit = slot.get("sizeLimit") or 2000
            if len(next_content) > limit:
                next_content = next_content[:limit]
            slot["content"] = next_content
            slot["updatedAt"] = now
            kv.set(target_kv, "session_patterns", slot)
            applied += 1

    if files:
        res = slot_get(kv, "project_context", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = (
                KV.globalSlots
                if scope == "global"
                else project_slots_scope(kv, project)
            )
            already = slot.get("content") or ""
            fresh = [f for f in files if f not in already][:20]
            if fresh:
                header_line = "Files touched in recent sessions:" if not already else ""
                sep = "\n" if already and not already.endswith("\n") else ""
                lines = [already]
                if header_line:
                    lines.append(header_line)
                for f in fresh:
                    lines.append(f"- {f}")
                next_content = sep.join([line for line in lines if line])
                limit = slot.get("sizeLimit") or 2000
                if len(next_content) > limit:
                    next_content = next_content[-limit:]
                slot["content"] = next_content
                slot["updatedAt"] = now
                kv.set(target_kv, "project_context", slot)
                applied += 1

    if applied > 0:
        safe_audit(
            kv,
            "slot_reflect",
            "mem::slot-reflect",
            [session_id],
            {"observationCount": len(recent), "slotsUpdated": applied},
        )
        commit_if_enabled(
            kv,
            f"Slot reflect: updated {applied} slots in session {session_id[:8]}",
            "system",
        )

    return {"success": True, "applied": applied, "observationsReviewed": len(recent)}
