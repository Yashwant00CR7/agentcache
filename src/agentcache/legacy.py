"""Re-export shim — behaviour lives in core/*. See ADR-0001."""

import threading
from typing import Any, Dict, Optional

from .core.audit_log import *  # noqa: F401,F403
from .core.config import *  # noqa: F401,F403
from .core.context_builder import *  # noqa: F401,F403
from .core.graph import *  # noqa: F401,F403
from .core.image_store import *  # noqa: F401,F403
from .core.infer import *  # noqa: F401,F403
from .core.kv_scopes import KV  # noqa: F401  re-exported for backward compat
from .core.lessons import *  # noqa: F401,F403
from .core.llm import *  # noqa: F401,F403
from .core.memory_store import *  # noqa: F401,F403
from .core.observation_store import (  # noqa: F401
    normalize_folder_path,
    validate_agent_id,
)
from .core.privacy import *  # noqa: F401,F403
from .core.project_profile import *  # noqa: F401,F403
from .core.search_service import IndexPersistence  # noqa: F401
from .core.session_store import *  # noqa: F401,F403
from .core.slots import *  # noqa: F401,F403
from .storage.paths import fingerprint_id, generate_id  # noqa: F401

# ---------------------------------------------------------------------------
# Module-level state — intentionally kept here (see ADR-0001 "Out of Scope").
# core/* modules access these via lazy `from .. import legacy as _legacy`.
# ---------------------------------------------------------------------------
_search_service = None  # type: ignore[assignment]  SearchService | None
_stream_broadcaster = None  # Callable: (payload) -> None
_dedup_locks: Dict[str, threading.Lock] = {}
_dedup_locks_meta = threading.Lock()


def set_search_service(service) -> None:
    """Register the SearchService instance created by app.init_services()."""
    global _search_service
    _search_service = service


def set_stream_broadcaster(broadcaster) -> None:
    global _stream_broadcaster
    _stream_broadcaster = broadcaster


def broadcast_stream(payload: Dict[str, Any]) -> None:
    if _stream_broadcaster:
        try:
            _stream_broadcaster(payload)
        except Exception as e:
            print(f"[broadcaster] Failed: {e}")


def backfill_obs_lookup_if_needed(kv) -> None:
    """Ensure every folder observation has an entry in KV.obs_lookup."""
    from .core.session_store import _get_observation_store

    store = _get_observation_store(kv)
    store.backfill_lookup()


def verify_index_sync_on_boot(kv, search_service: Optional[Any] = None) -> bool:
    """Check if the search index size matches the database counts."""
    try:
        svc = search_service or _search_service
        if svc is None:
            from . import app as app_module

            svc = getattr(app_module, "search_service", None)

        folders = kv.list(KV.folders)
        folder_obs_count = sum(int(f.get("obsCount", 0)) for f in folders)

        memories = kv.list(KV.memories)
        latest_memories_count = len(
            [m for m in memories if m.get("isLatest") is not False]
        )

        total_db_count = folder_obs_count + latest_memories_count
        index_size = svc.bm25_size if svc else 0

        if total_db_count != index_size:
            print(
                f"[persistence] Index out of sync with DB (DB={total_db_count}, Index={index_size}). Rebuild required."
            )
            return False

        print(f"[persistence] Index is in sync with DB (size={index_size}).")
        return True
    except Exception as e:
        print(f"[persistence] verify_index_sync_on_boot failed: {e}")
        return False
