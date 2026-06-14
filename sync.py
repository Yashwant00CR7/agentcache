#!/usr/bin/env python3
"""
Sync agentmemory data to/from a private HF Dataset repo.
Usage:
  python3 sync.py restore   -- download DB from HF on startup
  python3 sync.py backup    -- upload DB to HF (called in loop)

C4.1: Uses audit log high-water mark instead of mtime-based change detection.
C4.2: Exposes last sync status via a .sync_state JSON file read by /health.
"""
import json
import os
import sys
import shutil
import tempfile
import time
import sqlite3

try:
    from huggingface_hub import HfApi, snapshot_download, hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
except ImportError:
    print("[sync] huggingface_hub not installed, skipping sync")
    sys.exit(0)

HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO_ID  = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR = os.path.expanduser("~/.agentmemory")
DB_PATH  = os.path.join(DATA_DIR, "agentmemory.db")

# Only these paths are backed up/restored — everything else is ephemeral
SYNC_FILES = [
    "agentmemory.db",
    ".hmac",
]
SYNC_DIRS = [
    "second-brain",
]

# C4.1: High-water mark stored as JSON (replaces mtime STATE_FILE)
STATE_FILE = os.path.join(DATA_DIR, ".sync_state")


def get_api():
    return HfApi(token=HF_TOKEN)


def _collect_sync_targets():
    """Return list of (abs_path, repo_rel_path) for all files to sync."""
    targets = []
    for fname in SYNC_FILES:
        full = os.path.join(DATA_DIR, fname)
        if os.path.isfile(full):
            targets.append((full, fname))
    for dname in SYNC_DIRS:
        dpath = os.path.join(DATA_DIR, dname)
        if os.path.isdir(dpath):
            for root, _, files in os.walk(dpath):
                for f in files:
                    full = os.path.join(root, f)
                    rel  = os.path.relpath(full, DATA_DIR).replace("\\", "/")
                    targets.append((full, rel))
    return targets


def _get_audit_high_water_mark() -> int:
    """C4.1: Return MAX(id) from audit_log, or 0 if DB is absent/empty."""
    try:
        if not os.path.exists(DB_PATH):
            return 0
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        try:
            row = conn.execute("SELECT MAX(id) FROM audit_log").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _load_sync_state() -> dict:
    """C4.1: Load the persisted sync state dict."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_synced_audit_id": 0, "last_sync_at": None, "sync_status": "never"}


def _save_sync_state(state: dict) -> None:
    """C4.1/C4.2: Persist the sync state dict."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[sync] failed to save sync state: {e}")


def restore():
    if not HF_TOKEN:
        print("[sync] No HF_TOKEN — skipping restore")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    api = get_api()

    # Check repo exists
    try:
        api.repo_info(REPO_ID, repo_type="dataset")
    except RepositoryNotFoundError:
        print(f"[sync] Dataset repo {REPO_ID} not found — fresh start")
        return
    except Exception as e:
        print(f"[sync] restore repo check error: {e}")
        return

    # Download each sync target individually
    all_targets = SYNC_FILES + [
        f for f in _list_repo_prefix(api, "second-brain/")
    ]

    if not all_targets:
        print("[sync] Dataset empty — fresh start")
        return

    for fname in all_targets:
        try:
            local_path = os.path.join(DATA_DIR, fname)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            hf_hub_download(
                repo_id=REPO_ID,
                filename=fname,
                repo_type="dataset",
                token=HF_TOKEN,
                local_dir=DATA_DIR,
                local_dir_use_symlinks=False,
            )
            print(f"[sync] restored {fname}")
        except EntryNotFoundError:
            pass  # file not yet in repo, skip
        except Exception as e:
            print(f"[sync] restore {fname} error: {e}")

    print("[sync] restore complete")


def _list_repo_prefix(api, prefix):
    """List files in repo matching a path prefix."""
    try:
        from huggingface_hub import list_repo_files
        return [f for f in list_repo_files(REPO_ID, repo_type="dataset", token=HF_TOKEN)
                if f.startswith(prefix)]
    except Exception:
        return []


def backup():
    if not HF_TOKEN:
        return
    api = get_api()

    targets = _collect_sync_targets()
    if not targets:
        print("[sync] nothing to backup")
        return

    # C4.1: Compare audit log high-water mark instead of mtime fingerprint
    current_hwm = _get_audit_high_water_mark()
    state = _load_sync_state()
    last_hwm = state.get("last_synced_audit_id", 0)

    if current_hwm <= last_hwm:
        print(f"[sync] no new audit entries (hwm={current_hwm}) — skipping backup")
        return

    print(f"[sync] audit HWM changed ({last_hwm} → {current_hwm}) — backing up...")

    # Ensure repo exists
    try:
        api.repo_info(REPO_ID, repo_type="dataset")
    except RepositoryNotFoundError:
        print(f"[sync] Creating dataset repo {REPO_ID}")
        api.create_repo(REPO_ID, repo_type="dataset", private=True)
    except Exception as e:
        print(f"[sync] repo_info error: {e}")
        # C4.2: record error state
        state["sync_status"] = "error"
        _save_sync_state(state)
        return

    # Stage only the targeted files
    staging = tempfile.mkdtemp(prefix="agentmemory_sync_")
    try:
        for full, rel in targets:
            dest = os.path.join(staging, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.copy2(full, dest)
            except Exception as e:
                print(f"[sync] stage {rel} error: {e}")

        print(f"[sync] uploading {len(targets)} files to {REPO_ID}...")
        api.upload_folder(
            folder_path=staging,
            repo_id=REPO_ID,
            repo_type="dataset",
            token=HF_TOKEN,
            commit_message="sync: periodic backup",
        )
        print("[sync] backup complete")

        # C4.1/C4.2: update state with new HWM and timestamp
        import datetime
        state["last_synced_audit_id"] = current_hwm
        state["last_sync_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        state["sync_status"] = "ok"
        _save_sync_state(state)

    except Exception as e:
        print(f"[sync] backup error: {e}")
        state["sync_status"] = "error"
        _save_sync_state(state)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backup"
    if cmd == "restore":
        restore()
    elif cmd == "backup":
        backup()
    else:
        print(f"[sync] unknown command: {cmd}")
        sys.exit(1)


HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO_ID  = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR = os.path.expanduser("~/.agentmemory")
DB_PATH  = os.path.join(DATA_DIR, "agentmemory.db")
