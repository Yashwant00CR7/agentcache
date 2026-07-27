"""Context builder + XML parsing helpers for LLM consolidation output."""

import re
from typing import Any, Dict, List, Optional

from ..db import StateKV
from .kv_scopes import KV
from .observation_store import normalize_folder_path


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


def _summary_block(meta: Dict[str, Any]) -> Optional[str]:
    """Render a folder's stored summary metadata into a context block."""
    summary = meta.get("summary")
    if not summary:
        return None
    if isinstance(summary, dict):
        title = summary.get("title")
        narrative = summary.get("narrative") or summary.get("content") or ""
        header = f"## Prior summary: {title}" if title else "## Prior summary"
        body = narrative.strip()
        return f"{header}\n{body}" if body else header
    text = str(summary).strip()
    return f"## Prior summary\n{text}" if text else None


def context(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build injected context for a folder scope from stored memory.

    Ported off the old ``KV.sessions`` model onto folder-scoped storage
    (#41/#45). Identity mapping mirrors the ``agent_observe`` convention:
    ``folderPath = cwd or project`` and ``agentId = sessionId``.

    Blocks are assembled in priority order (prior folder summaries → relevant
    long-term memories → recent high-signal observations) and greedily packed
    into ``budget`` characters.
    """
    data = data or {}
    session_id = data.get("sessionId")
    project = data.get("project")
    cwd = data.get("cwd")
    try:
        budget = int(data.get("budget") or 1500)
    except (TypeError, ValueError):
        budget = 1500

    folder_path_raw = cwd or project
    if not folder_path_raw or not session_id:
        raise ValueError("(cwd or project) and sessionId are required")

    folder_path = normalize_folder_path(folder_path_raw)
    agent_id = session_id  # identity mapping

    from .session_store import _get_observation_store

    store = _get_observation_store(kv)

    blocks: List[str] = []

    # 1. Prior folder summaries (across every agent that worked this folder).
    for entry in kv.list(KV.folders):
        if entry.get("folderPath") != folder_path:
            continue
        aid = entry.get("agentId")
        if not aid:
            continue
        meta = kv.get(KV.folder_meta(folder_path, aid), "meta")
        if meta and isinstance(meta, dict):
            block = _summary_block(meta)
            if block:
                blocks.append(block)

    # 2. Relevant long-term memories (strongest first, project-scoped).
    memories = [
        m
        for m in kv.list(KV.memories)
        if m.get("isLatest") is not False and m.get("title")
    ]
    if project:
        memories = [
            m for m in memories if not m.get("project") or m.get("project") == project
        ]
    memories.sort(key=lambda m: m.get("strength", 5), reverse=True)
    for m in memories[:5]:
        blocks.append(f"## Memory: {m['title']}\n{m.get('content', '')}".strip())

    # 3. Recent high-signal observations for this folder.
    recent = store.timeline(limit=50, folder_path=folder_path)
    recent = [o for o in recent if o.get("importance", 5) >= 5][:8]
    if recent:
        lines = [
            f"- [{o.get('type', 'other')}] "
            f"{(o.get('title') or o.get('text', ''))[:120]}"
            for o in recent
        ]
        blocks.append("## Recent activity\n" + "\n".join(lines))

    # Greedy budget fill (never drop the first block just because it's large).
    packed: List[str] = []
    used = 0
    for block in blocks:
        if packed and used + len(block) > budget:
            break
        packed.append(block)
        used += len(block) + 2  # account for the joining newlines

    return {
        "context": "\n\n".join(packed),
        "blocks": len(packed),
        "folderPath": folder_path,
        "agentId": agent_id,
    }
