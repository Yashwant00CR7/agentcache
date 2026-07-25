"""Prompt context compilation — assembles memory blocks into XML context string."""

import os
import re
import time
from typing import Any, Dict, List, Optional

from ..db import StateKV
from .kv_scopes import KV
from .slots import list_pinned_slots, render_pinned_context


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3)


def escape_xml_attr(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def strip_xml_wrappers(raw: str) -> str:
    if not raw:
        return ""
    cleaned = raw.strip()
    cleaned = re.sub(r"```xml\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()
    root_match = re.search(
        r"(<[a-zA-Z_][a-zA-Z0-9_-]*>[\s\S]*<\/[a-zA-Z_][a-zA-Z0-9_-]*>)", cleaned
    )
    if root_match:
        return root_match.group(1).strip()
    return cleaned


def get_xml_tag(text: str, tag: str) -> Optional[str]:
    cleaned = strip_xml_wrappers(text)
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, cleaned, re.DOTALL)
    return match.group(1).strip() if match else None


def get_xml_children(text: str, parent_tag: str, child_tag: str) -> List[str]:
    parent_content = get_xml_tag(text, parent_tag)
    if not parent_content:
        return []
    pattern = rf"<{child_tag}>(.*?)</{child_tag}>"
    return [m.strip() for m in re.findall(pattern, parent_content, re.DOTALL)]


def context(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    session_id = data.get("sessionId")
    project = data.get("project")
    budget = data.get("budget") or int(os.getenv("TOKEN_BUDGET", "2000"))

    if not session_id or not project:
        raise ValueError("sessionId and project are required")

    blocks = []

    # 1. Pinned Slots
    pinned_slots = list_pinned_slots(kv, project)
    slot_content = render_pinned_context(pinned_slots)
    if slot_content:
        blocks.append(
            {
                "type": "memory",
                "content": slot_content,
                "tokens": estimate_tokens(slot_content),
                "recency": int(time.time() * 1000),
            }
        )

    # 2. Profile
    profile = kv.get(KV.profiles, project)
    if profile:
        profile_parts = []
        if profile.get("topConcepts"):
            profile_parts.append(
                "Concepts: "
                + ", ".join([c["concept"] for c in profile["topConcepts"][:8]])
            )
        if profile.get("topFiles"):
            profile_parts.append(
                "Key files: " + ", ".join([f["file"] for f in profile["topFiles"][:5]])
            )
        if profile.get("conventions"):
            profile_parts.append("Conventions: " + "; ".join(profile["conventions"]))
        if profile.get("commonErrors"):
            profile_parts.append(
                "Common errors: " + "; ".join(profile["commonErrors"][:3])
            )

        if profile_parts:
            profile_content = "## Project Profile\n" + "\n".join(profile_parts)
            blocks.append(
                {
                    "type": "memory",
                    "content": profile_content,
                    "tokens": estimate_tokens(profile_content),
                    "recency": int(time.time() * 1000),
                }
            )

    # 3. Lessons
    lessons = kv.list(KV.lessons)
    relevant_lessons = [
        les
        for les in lessons
        if not les.get("deleted")
        and (not les.get("project") or les["project"] == project)
    ]

    def lesson_score(les):
        factor = 1.5 if les.get("project") == project else 1.0
        return factor * les.get("confidence", 0.5)

    relevant_lessons.sort(key=lesson_score, reverse=True)
    relevant_lessons = relevant_lessons[:10]

    if relevant_lessons:
        items = []
        for les in relevant_lessons:
            desc = f"- ({les['confidence']:.2f}) {les['content']}"
            if les.get("context"):
                desc += f" — {les['context']}"
            items.append(desc)
        lessons_content = "## Lessons Learned\n" + "\n".join(items)
        blocks.append(
            {
                "type": "memory",
                "content": lessons_content,
                "tokens": estimate_tokens(lessons_content),
                "recency": int(time.time() * 1000),
            }
        )

    # 4. Sessions & Summaries
    all_sessions = kv.list(KV.sessions)
    sessions = [
        s for s in all_sessions if s.get("project") == project and s["id"] != session_id
    ]
    sessions.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
    sessions = sessions[:10]

    for s in sessions:
        summary = kv.get(KV.summaries, s["id"])
        if summary:
            content = (
                f"## {summary.get('title', 'Session summary')}\n{summary.get('narrative', '')}\n"
                f"Decisions: {'; '.join(summary.get('keyDecisions', []))}\n"
                f"Files: {', '.join(summary.get('filesModified', []))}"
            )
            blocks.append(
                {
                    "type": "summary",
                    "content": content,
                    "tokens": estimate_tokens(content),
                    "recency": int(time.time() * 1000),
                }
            )
        else:
            obs_list = kv.list(KV.observations(s["id"]))
            important = [
                o for o in obs_list if o.get("title") and o.get("importance", 0) >= 5
            ]
            if important:
                important.sort(key=lambda o: o.get("importance", 0), reverse=True)
                top = important[:5]
                items = [
                    f"- [{o.get('type')}] {o.get('title')}: {o.get('narrative')}"
                    for o in top
                ]
                content = (
                    f"## Session {s['id'][:8]} ({s.get('startedAt')})\n"
                    + "\n".join(items)
                )
                blocks.append(
                    {
                        "type": "observation",
                        "content": content,
                        "tokens": estimate_tokens(content),
                        "recency": int(time.time() * 1000),
                    }
                )

    blocks.sort(key=lambda b: b.get("recency", 0), reverse=True)

    header = f'<agentcache-context project="{escape_xml_attr(project)}">'
    footer = "</agentcache-context>"
    used_tokens = estimate_tokens(header) + estimate_tokens(footer)

    selected = []
    for b in blocks:
        if used_tokens + b["tokens"] > budget:
            continue
        selected.append(b["content"])
        used_tokens += b["tokens"]

    if not selected:
        return {"context": "", "blocks": 0, "tokens": 0}

    res_context = f"{header}\n" + "\n\n".join(selected) + f"\n{footer}"
    return {"context": res_context, "blocks": len(selected), "tokens": used_tokens}
