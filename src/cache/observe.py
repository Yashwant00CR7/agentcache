"""
src/memory/observe.py — Observation ingestion pipeline.

Public API:
  folder_observe(kv, payload)         — ingest a folder-scoped observation
  observe(kv, payload)                — legacy session-scoped observation
  build_synthetic_compression(raw)    — build compressed observation dict
  strip_private_data(text)            — redact secrets and private tags
"""

from __future__ import annotations

from typing import Any, Dict

from db import StateKV
import functions as _fn  # access module-level globals (_bm25_index, etc.)


# Re-export for backward compatibility
strip_private_data = _fn.strip_private_data
build_synthetic_compression = _fn.build_synthetic_compression


def folder_observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a new observation scoped to a (folder_path, agent_id) pair."""
    return _fn.folder_observe(kv, payload)


def observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy session-scoped observation ingestion."""
    return _fn.observe(kv, payload)
