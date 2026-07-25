"""
Shared service accessors for route blueprints.

Route handlers call these to reach the process-wide singletons that
``app.py:init_services`` populates on the Flask ``extensions`` dict.
Kept here so no route file has to re-derive the "extensions → app-module
globals → raise" fallback chain that used to live in six copies.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _from_extensions(name: str) -> Any:
    ext = getattr(current_app, "extensions", {}) or {}
    value = ext.get(name)
    if value is not None:
        return value

    from .. import app as app_module

    return getattr(app_module, name, None)


def get_kv() -> Any:
    """Return the process StateKV. Raises RuntimeError if uninitialised."""
    kv = _from_extensions("kv")
    if kv is None:
        raise RuntimeError("StateKV is not initialized")
    return kv


def get_search_service() -> Any:
    """Return the SearchService singleton (or None if search is disabled)."""
    return _from_extensions("search_service")


def get_observation_store() -> Any:
    """Return the ObservationStore. Raises RuntimeError if uninitialised."""
    store = _from_extensions("observation_store")
    if store is None:
        raise RuntimeError("ObservationStore is not initialized")
    return store
