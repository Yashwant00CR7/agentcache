#!/usr/bin/env python3
"""
Sync agentmemory data to/from a private HF Dataset repo.
Usage:
  python3 sync.py restore   -- download DB from HF on startup
  python3 sync.py backup    -- upload DB to HF (called in loop)
"""
import json
import os
import sys
import shutil
import tempfile
import time

try:
    from huggingface_hub import HfApi, snapshot_download, hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
except ImportError:
    print("[sync] huggingface_hub not installed, skipping sync")
    sys.exit(0)

HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO_ID  = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR = os.path.expanduser("~/.agentmemory")

# Only these paths are backed up/restored — everything else is ephemeral
SYNC_FILES = [
    "agentmemory.db",
    ".hmac",
]
SYNC_DIRS = [
    "second-brain",
]

STATE_FILE = os.path.join(DATA_DIR, ".backup_state")

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

def _state_fingerprint(targets):
    entries = {}
    for full, rel in targets:
        try:
            s = os.stat(full)
            entries[rel] = (s.st_size, s.st_mtime)
        except OSError:
            pass
    return json.dumps(entries, sort_keys=True)

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

    # Fast change detection
    current_state = _state_fingerprint(targets)
    if os.path.exists(STATE_FILE):
        try:
            if open(STATE_FILE).read() == current_state:
                print("[sync] no changes — skipping backup")
                return
        except Exception:
            pass

    # Ensure repo exists
    try:
        api.repo_info(REPO_ID, repo_type="dataset")
    except RepositoryNotFoundError:
        print(f"[sync] Creating dataset repo {REPO_ID}")
        api.create_repo(REPO_ID, repo_type="dataset", private=True)
    except Exception as e:
        print(f"[sync] repo_info error: {e}")
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
        try:
            open(STATE_FILE, "w").write(current_state)
        except Exception:
            pass
    except Exception as e:
        print(f"[sync] backup error: {e}")
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
