"""Folder graph builder for the viewer's Graph tab."""

import os
from typing import Any, Dict, List, Set, Tuple

from ..db import StateKV
from .config import get_agent_id, is_agent_scope_isolated
from .kv_scopes import KV


def folder_color(path: str) -> str:
    """Hash a folder path string to an HSL color string.

    Replicates the JS ``folderColor(id)`` function in src/viewer/index.html
    exactly, using the light-mode lightness range (38 + h%14).
    """
    h = 0
    for ch in path:
        h = (h * 31 + ord(ch)) & 0xFFFFFFF

    hue = (h % 360 + 360) % 360
    sat_pct = 55 + (h % 25)
    lig_pct = 38 + (h % 14)

    return f"hsl({hue}, {sat_pct}%, {lig_pct}%)"


def folder_graph_build(kv: StateKV) -> Dict[str, Any]:
    """Build graph data for the viewer's Graph tab."""
    index_entries = kv.list(KV.folders)
    if is_agent_scope_isolated():
        aid = get_agent_id()
        if aid:
            index_entries = [e for e in index_entries if e.get("agentId") == aid]

    folder_map: Dict[str, Dict[str, Any]] = {}
    pair_obs_texts: Dict[Tuple[str, str], str] = {}

    for entry in index_entries:
        fp = entry.get("folderPath", "")
        aid = entry.get("agentId", "")
        if not fp:
            continue

        if fp not in folder_map:
            folder_map[fp] = {
                "folderPath": fp,
                "agentIds": set(),
                "obsCount": 0,
                "color": folder_color(fp),
            }

        folder_map[fp]["agentIds"].add(aid)
        folder_map[fp]["obsCount"] += entry.get("obsCount", 0)

        obs_scope = KV.folder_obs(fp, aid)
        obs_list = kv.list(obs_scope)
        combined_parts = []
        for obs in obs_list:
            text = obs.get("text") or ""
            title = obs.get("title") or ""
            combined_parts.append(f"{text} {title}")
        pair_obs_texts[(fp, aid)] = " ".join(combined_parts)

    nodes = []
    for fp, info in folder_map.items():
        nodes.append(
            {
                "id": fp,
                "label": os.path.basename(fp) or fp,
                "folderPath": fp,
                "agentIds": sorted(info["agentIds"]),
                "obsCount": info["obsCount"],
                "color": info["color"],
            }
        )

    edges: List[Dict[str, Any]] = []
    seen_edges: Set[Tuple[Any, str]] = set()

    def add_edge(edge: Dict[str, Any]) -> None:
        key = (frozenset([edge["source"], edge["target"]]), edge["type"])
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append(edge)

    folder_paths = list(folder_map.keys())

    for i in range(len(folder_paths)):
        for j in range(i + 1, len(folder_paths)):
            a = folder_paths[i]
            b = folder_paths[j]
            if a.rsplit("/", 1)[0] == b.rsplit("/", 1)[0] and "/" in a and "/" in b:
                add_edge({"source": a, "target": b, "type": "same-parent"})
            elif os.path.dirname(a) == os.path.dirname(b) and os.path.dirname(a) != "":
                add_edge({"source": a, "target": b, "type": "same-parent"})

    for (fp_a, _agent_a), text_a in pair_obs_texts.items():
        for fp_b in folder_paths:
            if fp_b != fp_a and fp_b in text_a:
                add_edge({"source": fp_a, "target": fp_b, "type": "cross-ref"})

    agent_to_folders: Dict[str, List[str]] = {}
    for fp, info in folder_map.items():
        for aid in info["agentIds"]:
            agent_to_folders.setdefault(aid, []).append(fp)

    for aid, fps in agent_to_folders.items():
        for i in range(len(fps)):
            for j in range(i + 1, len(fps)):
                add_edge(
                    {
                        "source": fps[i],
                        "target": fps[j],
                        "type": "agent-shared",
                        "agentId": aid,
                    }
                )

    return {"nodes": nodes, "edges": edges}
