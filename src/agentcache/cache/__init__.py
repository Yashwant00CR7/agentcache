"""
src/cache/ — Core business logic package for agentcache-python.

Sub-modules:
  observe   — folder_observe(), observe(), build_synthetic_compression(), strip_private_data()
  remember  — remember(), forget(), jaccard_similarity()
  context   — context(), export_data(), rebuild_index()
  graph     — folder_graph_build(), get_relations(), add_relation()
  timeline  — folder_timeline(), folder_search()
  health    — health_check(), auto_forget()

Compatibility shim: also re-exports everything from functions.py that
callers may import from this package (A2.2).
"""

# ---------------------------------------------------------------------------
# Compatibility shim — delegate additional names to functions.py (A2.2)
# Each name is imported lazily via a try/except so missing items don't break
# the package import on partially-initialised environments.
# ---------------------------------------------------------------------------
from .. import legacy as _fn  # noqa: E402
from ..core import KV
from .context import context, export_data, rebuild_index
from .graph import folder_graph_build
from .health import auto_forget, health_check
from .observe import (
    build_synthetic_compression,
    folder_observe,
    observe,
    strip_private_data,
)
from .remember import forget, jaccard_similarity, remember
from .timeline import folder_search, folder_timeline

generate_id = _fn.generate_id
fingerprint_id = _fn.fingerprint_id
normalize_folder_path = _fn.normalize_folder_path
validate_agent_id = _fn.validate_agent_id
set_stream_broadcaster = _fn.set_stream_broadcaster
get_agent_id = _fn.get_agent_id
record_audit = _fn.record_audit
query_audit = _fn.query_audit
safe_audit = _fn.safe_audit
lesson_save = _fn.lesson_save
lesson_list = _fn.lesson_list
lesson_recall = _fn.lesson_recall
migrate_sessions_to_folders = _fn.migrate_sessions_to_folders
list_sessions = _fn.list_sessions

__all__ = [
    # observe.py
    "folder_observe",
    "observe",
    "build_synthetic_compression",
    "strip_private_data",
    # remember.py
    "remember",
    "forget",
    "jaccard_similarity",
    # context.py
    "context",
    "export_data",
    "rebuild_index",
    # graph.py
    "folder_graph_build",
    # timeline.py
    "folder_timeline",
    "folder_search",
    # health.py
    "health_check",
    "auto_forget",
    # legacy.py shims
    "KV",
    "generate_id",
    "fingerprint_id",
    "normalize_folder_path",
    "validate_agent_id",
    "set_stream_broadcaster",
    "get_agent_id",
    "record_audit",
    "query_audit",
    "safe_audit",
    "lesson_save",
    "lesson_list",
    "lesson_recall",
    "migrate_sessions_to_folders",
    "list_sessions",
]
