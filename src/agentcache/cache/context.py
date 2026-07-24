"""
src/memory/context.py — Context compilation, export, and index rebuilding.

Public API:
  context(kv, data)     — compile token-budgeted context for a session/project
  export_data(kv, data) — export all folder observations + memories as JSON
  rebuild_index(kv)     — cold-rebuild BM25/vector index from KV store
"""

from __future__ import annotations

from typing import Any, Dict

from .. import legacy as _fn
from ..db import StateKV


def context(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble context for a session: slots → profile → lessons → past summaries."""
    return _fn.context(kv, data)


def export_data(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Export all folder observations and global memories as JSON (v2 format)."""
    return _fn.export_data(kv, data)


def rebuild_index(kv: StateKV) -> int:
    """Rebuild BM25/vector index from all KV observations and memories."""
    return _fn.rebuild_index(kv)
