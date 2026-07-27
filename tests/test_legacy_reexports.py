"""Static re-export manifest for agentcache.legacy.

Verifies that every public symbol is still reachable via legacy, and that
symbols with a known new home in core/* are the *same object* (not copies).
This is the guardrail against silent shim breakage.
"""

import importlib

import pytest

import agentcache.legacy

# ---------------------------------------------------------------------------
# Manifest: symbol -> dotted module path where it now lives.
# After the extraction legacy.py re-exports each of these via `from .core.X import *`.
# ---------------------------------------------------------------------------
SYMBOL_MAP = {
    # core/privacy.py
    "PRIVATE_TAG_RE": "agentcache.core.privacy",
    "SECRET_PATTERN_SOURCES": "agentcache.core.privacy",
    "strip_private_data": "agentcache.core.privacy",
    # core/config.py
    "get_agent_id": "agentcache.core.config",
    "is_agent_scope_isolated": "agentcache.core.config",
    "is_auto_compress_enabled": "agentcache.core.config",
    "is_graph_extraction_enabled": "agentcache.core.config",
    "is_consolidation_enabled": "agentcache.core.config",
    "commit_if_enabled": "agentcache.core.config",
    # core/image_store.py
    "IMAGES_DIR": "agentcache.core.image_store",
    "get_max_bytes": "agentcache.core.image_store",
    "is_managed_image_path": "agentcache.core.image_store",
    "save_image_to_disk": "agentcache.core.image_store",
    "delete_image": "agentcache.core.image_store",
    "touch_image": "agentcache.core.image_store",
    "extract_image": "agentcache.core.image_store",
    # core/audit_log.py
    "record_audit": "agentcache.core.audit_log",
    "safe_audit": "agentcache.core.audit_log",
    "query_audit": "agentcache.core.audit_log",
    # core/infer.py
    "infer_type": "agentcache.core.infer",
    "extract_files": "agentcache.core.infer",
    "stringify_for_narrative": "agentcache.core.infer",
    "clip_embed_input": "agentcache.core.infer",
    "vector_index_add_guarded": "agentcache.core.infer",
    "build_synthetic_compression": "agentcache.core.infer",
    # core/memory_store.py
    "remember": "agentcache.core.memory_store",
    "memory_to_observation": "agentcache.core.memory_store",
    "jaccard_similarity": "agentcache.core.memory_store",
    "evolve_memory": "agentcache.core.memory_store",
    # core/lessons.py
    "lesson_save": "agentcache.core.lessons",
    "lesson_list": "agentcache.core.lessons",
    "lesson_recall": "agentcache.core.lessons",
    "lesson_strengthen": "agentcache.core.lessons",
    "lesson_decay_sweep": "agentcache.core.lessons",
    "reinforce_lesson": "agentcache.core.lessons",
    # core/context_builder.py
    "strip_xml_wrappers": "agentcache.core.context_builder",
    "get_xml_tag": "agentcache.core.context_builder",
    "get_xml_children": "agentcache.core.context_builder",
    # core/session_store.py
    "observe": "agentcache.core.session_store",
    "list_sessions": "agentcache.core.session_store",
    "get_session": "agentcache.core.session_store",
    "create_session": "agentcache.core.session_store",
    "end_session": "agentcache.core.session_store",
    "timeline": "agentcache.core.session_store",
    "auto_complete_old_active_sessions": "agentcache.core.session_store",
    "folder_observe": "agentcache.core.session_store",
    "folder_search": "agentcache.core.session_store",
    "folder_timeline": "agentcache.core.session_store",
    "dedup_folder_observations": "agentcache.core.session_store",
    # core/project_profile.py
    "get_project_profile": "agentcache.core.project_profile",
    "build_project_profile": "agentcache.core.project_profile",
    "set_project_profile": "agentcache.core.project_profile",
    "export_data": "agentcache.core.project_profile",
    "migrate_sessions_to_folders": "agentcache.core.project_profile",
    "get_relations": "agentcache.core.project_profile",
    "add_relation": "agentcache.core.project_profile",
    "auto_forget": "agentcache.core.project_profile",
    "health_check": "agentcache.core.project_profile",
    "rebuild_index": "agentcache.core.project_profile",
    # core/llm.py
    "generate_content": "agentcache.core.llm",
    "consolidate": "agentcache.core.llm",
    # core/graph.py
    "folder_color": "agentcache.core.graph",
    "folder_graph_build": "agentcache.core.graph",
}

# Symbols that must exist on legacy but have no new module to cross-check
LEGACY_ONLY_SYMBOLS = [
    "generate_id",
    "fingerprint_id",
    "normalize_folder_path",
    "validate_agent_id",
    "KV",
    "IndexPersistence",
    "set_search_service",
    "set_stream_broadcaster",
    "broadcast_stream",
    "backfill_obs_lookup_if_needed",
    "verify_index_sync_on_boot",
]


@pytest.mark.parametrize("name", list(SYMBOL_MAP.keys()) + LEGACY_ONLY_SYMBOLS)
def test_symbol_exists_on_legacy(name):
    """Every catalogued symbol must be reachable via agentcache.legacy."""
    assert hasattr(agentcache.legacy, name), (
        f"agentcache.legacy.{name} is missing — shim re-export may have been dropped"
    )


@pytest.mark.parametrize("name,module_path", list(SYMBOL_MAP.items()))
def test_symbol_is_same_object(name, module_path):
    """Symbols with a known new home must be the *same object* in both places."""
    mod = importlib.import_module(module_path)
    legacy_obj = getattr(agentcache.legacy, name)
    core_obj = getattr(mod, name)
    assert legacy_obj is core_obj, (
        f"agentcache.legacy.{name} is not the same object as {module_path}.{name}; "
        "the shim re-export is broken"
    )
