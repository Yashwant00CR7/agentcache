"""
src/memory/timeline.py — Folder activity timeline and search.

Public API:
  folder_timeline(kv, limit, folder_path, agent_id, before, after)
  folder_search(kv, query, limit, folder_path, agent_id)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import functions as _fn
from ..db import StateKV


def folder_timeline(
    kv: StateKV,
    limit: int = 100,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return folder observations sorted newest-first with optional filters."""
    return _fn.folder_timeline(kv, limit, folder_path, agent_id, before, after)


def folder_search(
    kv: StateKV,
    query: str,
    limit: int = 20,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """BM25 + vector hybrid search across folder observations and global memories."""
    return _fn.folder_search(
        kv, query, limit, folder_path=folder_path, agent_id=agent_id
    )
