"""
src/memory/health.py — Health check and auto-forget sweep.

Public API:
  health_check(kv)       — return system health stats
  auto_forget(kv, ...)   — evict stale/low-importance observations
"""

from __future__ import annotations

from typing import Any, Dict

from db import StateKV
import functions as _fn


def health_check(kv: StateKV) -> Dict[str, Any]:
    """Return folder count, observation count, memory count, and subsystem status."""
    return _fn.health_check(kv)


def auto_forget(kv: StateKV, dry_run: bool = False) -> Dict[str, Any]:
    """Sweep and evict stale or low-importance observations."""
    return _fn.auto_forget(kv, dry_run=dry_run)
