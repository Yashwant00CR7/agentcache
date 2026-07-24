"""
src/storage/scopes.py — KV scope registry (A2.3).

Re-exports KV from core.kv_scopes for backward compatibility.
"""

from ..core.kv_scopes import KV

__all__ = ["KV"]
