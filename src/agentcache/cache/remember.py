"""
src/memory/remember.py — Long-term memory management.

Public API:
  remember(kv, data)          — save/update a global memory (versioned by Jaccard sim)
  forget(kv, data)            — delete memory/folder-pair/observations
  jaccard_similarity(a, b)    — word-set overlap ratio
"""

from __future__ import annotations

from typing import Any, Dict

from .. import legacy as _fn
from ..db import StateKV


def remember(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Save a global memory, superseding any existing memory with > 0.7 similarity."""
    return _fn.remember(kv, data)


def forget(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a global memory, a folder pair, or specific observations."""
    return _fn.forget(kv, data)


def jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings (word sets, min length 3)."""
    return _fn.jaccard_similarity(a, b)
