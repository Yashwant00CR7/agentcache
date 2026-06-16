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

from .observe import folder_observe, observe, build_synthetic_compression, strip_private_data
from .remember import remember, forget, jaccard_similarity
from .context import context, export_data, rebuild_index
from .graph import folder_graph_build
from .timeline import folder_timeline, folder_search
from .health import health_check, auto_forget

# ---------------------------------------------------------------------------
# Compatibility shim — delegate additional names to functions.py (A2.2)
# Each name is imported lazily via a try/except so missing items don't break
# the package import on partially-initialised environments.
# ---------------------------------------------------------------------------
import functions as _fn  # noqa: E402  (functions.py is on sys.path via src/)

KV = _fn.KV
generate_id = _fn.generate_id
fingerprint_id = _fn.fingerprint_id
normalize_folder_path = _fn.normalize_folder_path
validate_agent_id = _fn.validate_agent_id
IndexPersistence = _fn.IndexPersistence
set_embedding_provider = _fn.set_embedding_provider
set_index_persistence = _fn.set_index_persistence
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
    "folder_observe", "observe", "build_synthetic_compression", "strip_private_data",
    # remember.py
    "remember", "forget", "jaccard_similarity",
    # context.py
    "context", "export_data", "rebuild_index",
    # graph.py
    "folder_graph_build",
    # timeline.py
    "folder_timeline", "folder_search",
    # health.py
    "health_check", "auto_forget",
    # functions.py shims (A2.2)
    "KV",
    "generate_id", "fingerprint_id",
    "normalize_folder_path", "validate_agent_id",
    "IndexPersistence",
    "set_embedding_provider", "set_index_persistence", "set_stream_broadcaster",
    "get_agent_id",
    "record_audit", "query_audit", "safe_audit",
    "lesson_save", "lesson_list", "lesson_recall",
    "migrate_sessions_to_folders", "list_sessions",
]
