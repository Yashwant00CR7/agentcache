"""
Background worker threads for agentcache-python.

Start all workers via start_background_workers(kv).
Each worker runs as a daemon thread so it exits automatically when the process dies.

C5.1: SIGTERM and SIGINT handlers are registered here to flush the index
persistence debounce queue and run a WAL checkpoint before exit.
"""

import os
import signal
import sys
import threading
import time

from . import legacy

# Module-level shutdown flag — set by signal handlers
_shutting_down = threading.Event()

_persistence_ref = None
_search_svc_ref = None
_obs_store_ref = None
_kv_ref = None


def _shutdown_handler(signum, frame) -> None:  # noqa: ARG001
    """Handle SIGTERM/SIGINT gracefully (C5.1).

    Steps:
    1. Set the global _shutting_down flag to stop background loops.
    2. Flush the debounce timer and save the index synchronously via SearchService.flush_persist().
    3. Run a WAL checkpoint via StateKV.teardown().
    4. Exit cleanly with code 0.
    """
    sig_name = signal.Signals(signum).name
    print(f"\n[workers] Received {sig_name} — initiating graceful shutdown...")

    _shutting_down.set()

    global _search_svc_ref, _persistence_ref
    if _search_svc_ref is not None:
        try:
            print("[workers] Flushing SearchService persistence...")
            _search_svc_ref.flush_persist()
            print("[workers] SearchService persistence flushed.")
        except Exception as e:
            print(f"[workers] Error flushing SearchService: {e}")
    elif _persistence_ref is not None:
        try:
            print("[workers] Flushing index persistence...")
            _persistence_ref.flush()
            print("[workers] Index persistence flushed.")
        except Exception as e:
            print(f"[workers] Error flushing persistence: {e}")

    # WAL checkpoint — flush WAL to the main DB file
    global _kv_ref
    if _kv_ref is not None:
        try:
            print("[workers] Running WAL checkpoint...")
            _kv_ref.teardown()
            print("[workers] WAL checkpoint complete.")
        except Exception as e:
            print(f"[workers] Error during WAL checkpoint: {e}")

    print("[workers] Shutdown complete.")
    sys.exit(0)


def _register_signal_handlers() -> None:
    """Register SIGTERM and SIGINT handlers (C5.1).

    Skipped if the calling thread is not the main thread, because Python
    only allows signal handlers to be registered from the main thread.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        signal.signal(signal.SIGTERM, _shutdown_handler)
        signal.signal(signal.SIGINT, _shutdown_handler)
        print("[workers] Signal handlers registered (SIGTERM, SIGINT).")
    except (OSError, ValueError) as e:
        # Some environments (e.g. Windows without signal support) may raise here
        print(f"[workers] Could not register signal handlers: {e}")


def _auto_forget_loop(kv) -> None:
    """Periodically sweep and evict stale observations (configurable via AUTO_FORGET_ENABLED)."""
    time.sleep(10)
    while not _shutting_down.is_set():
        try:
            if os.getenv("AUTO_FORGET_ENABLED") != "false":
                if kv.acquire_lock("auto_forget", lease_seconds=300):
                    try:
                        print("[scheduler] Running auto_forget sweep...")
                        res = legacy.auto_forget(kv, dry_run=False)
                        print(f"[scheduler] auto_forget sweep completed: {res}")
                    finally:
                        kv.release_lock("auto_forget")
        except Exception as e:
            print(f"[scheduler] auto_forget loop error: {e}")
        # Sleep in 10-second chunks so we notice _shutting_down quickly
        for _ in range(360):  # 360 × 10s = 1 hour
            if _shutting_down.is_set():
                break
            time.sleep(10)


def _rebuild_index(kv) -> None:
    """Rebuild the BM25/vector index from scratch in a background thread."""
    try:
        if kv.acquire_lock("index_rebuild", lease_seconds=600):
            try:
                from . import app as app_module

                obs_store = getattr(app_module, "observation_store", None)
                if obs_store is not None:
                    count = obs_store.rebuild_index()
                else:
                    count = 0
                print(f"[persistence] Rebuild completed: indexed {count} items.")
            finally:
                kv.release_lock("index_rebuild")
        else:
            print("[persistence] Another process is rebuilding the index. Skipping...")
    except Exception as ex:
        print(f"[persistence] Rebuild failed: {ex}")


def start_background_workers(kv, tasks=None) -> None:
    """Start all background daemon threads and register signal handlers.

    Called once by create_app() after the DB and indexes are initialised.
    Workers are daemon threads — they die automatically when the main process exits.

    Args:
        kv: Initialised StateKV instance.
        tasks: Optional list of tasks to run ("index", "forget"). Defaults to running both.
    """
    global _kv_ref, _persistence_ref, _search_svc_ref, _obs_store_ref
    _kv_ref = kv

    from . import app as app_module

    search_svc = getattr(app_module, "search_service", None)
    obs_store = getattr(app_module, "observation_store", None)

    _search_svc_ref = search_svc
    _obs_store_ref = obs_store

    if search_svc is not None:
        _persistence_ref = search_svc._persistence
    else:
        _persistence_ref = None

    # Register graceful shutdown signal handlers (C5.1)
    _register_signal_handlers()

    if tasks is None or "index" in tasks:
        # Rebuild search index if empty or out of sync (Step 5)
        bm25_size = search_svc.bm25_size if search_svc is not None else 0
        index_empty = bm25_size == 0
        index_in_sync = True
        if not index_empty:
            index_in_sync = legacy.verify_index_sync_on_boot(
                kv, search_service=search_svc
            )

        if index_empty or not index_in_sync:
            reason = "empty" if index_empty else "out of sync"
            print(
                f"[persistence] Search index is {reason}. Rebuilding in background thread..."
            )
            t_rebuild = threading.Thread(
                target=_rebuild_index,
                args=(kv,),
                daemon=True,
                name="index-rebuild",
            )
            t_rebuild.start()

    if tasks is None or "forget" in tasks:
        # Auto-forget sweep
        t_forget = threading.Thread(
            target=_auto_forget_loop,
            args=(kv,),
            daemon=True,
            name="auto-forget",
        )
        t_forget.start()
