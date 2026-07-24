"""
src/memory/graph.py — Knowledge graph construction.

Public API:
  folder_graph_build(kv)    — build graph nodes+edges from folder observations
"""

from __future__ import annotations

from typing import Any, Dict

from .. import legacy as _fn
from ..db import StateKV


def folder_graph_build(kv: StateKV) -> Dict[str, Any]:
    """Build a folder-based knowledge graph with nodes, edges, and color assignments."""
    return _fn.folder_graph_build(kv)
