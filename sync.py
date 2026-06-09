#!/usr/bin/env python3
"""
Sync ~/.agentmemory/ data to/from a private HF Dataset repo.
Usage:
  python3 sync.py restore   -- download DB from HF on startup
  python3 sync.py backup    -- upload DB to HF (called in loop)
"""
import json
import os
import sys
import shutil
import tempfile

try:
    from huggingface_hub import HfApi, hf_hub_download, list_repo_files
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
except ImportError:
    print("[sync] huggingface_hub not installed, skipping sync")
    sys.exit(0)

HF_TOKEN     = os.environ.get("HF_TOKEN", "")
REPO_ID      = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR     = os.path.expanduser("~/.agentmemory")
SKIP_FILES   = {".env"}
ALLOW_HIDDEN = {".hmac"}
SKIP_NAMES   = {"LOCK"}  # held open by Dolt — always skip

def _quick_hash(data_dir):
    """Build a fingerprint from file sizes+mtimes — fast, no file reads."""
    entries = {}
    for root, dirs, files in os.walk(data_dir):
        is_inside_dolt = ".dolt" in root.replace("\\", "/").split("/")
        if not is_inside_dolt:
            dirs[:] = [d for d in dirs if not d.startswith(".") or d == ".dolt"]
        for f in files:
            if f in SKIP_FILES or f in SKIP_NAMES:
                continue
            if f.startswith(".") and f not in ALLOW_HIDDEN:
                continue
            full = os.path.join(root, f)
            rel  = os.path.relpath(full, data_dir).replace("\\", "/")
            try:
                s = os.stat(full)
                entries[rel] = (s.st_size, s.st_mtime)
            except OSError:
                pass
    return json.dumps(entries, sort_keys=True)

def get_api():
    return HfApi(token=HF_TOKEN)

def restore():
    if not HF_TOKEN:
        print("[sync] No HF_TOKEN — skipping restore")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    api = get_api()
    try:
        files = list(list_repo_files(REPO_ID, repo_type="dataset", token=HF_TOKEN))
    except RepositoryNotFoundError:
        print(f"[sync] Dataset repo {REPO_ID} not found — will create on first backup")
        return
    except Exception as e:
        print(f"[sync] restore list error: {e}")
        return

    if not files:
        print("[sync] Dataset empty — fresh start")
        return

    for fname in files:
        try:
            local_path = os.path.join(DATA_DIR, fname)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            hf_hub_download(
                repo_id=REPO_ID,
                filename=fname,
                repo_type="dataset",
                token=HF_TOKEN,
                local_dir=DATA_DIR,
            )
            print(f"[sync] restored {fname}")
        except Exception as e:
            print(f"[sync] restore {fname} error: {e}")

    print("[sync] restore complete")

def backup():
    if not HF_TOKEN:
        return
    api = get_api()

    # Fast change detection — skip everything if nothing modified
    state_file = os.path.join(DATA_DIR, ".backup_state")
    current_state = _quick_hash(DATA_DIR)
    if os.path.exists(state_file):
        try:
            if open(state_file).read() == current_state:
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

    # Stage files into a temp dir then upload in one commit
    staging = tempfile.mkdtemp(prefix="agentmemory_sync_")
    try:
        copied = 0
        for root, dirs, files in os.walk(DATA_DIR):
            is_inside_dolt = ".dolt" in root.replace("\\", "/").split("/")
            if not is_inside_dolt:
                dirs[:] = [d for d in dirs if not d.startswith(".") or d == ".dolt"]
            for f in files:
                if f in SKIP_FILES or f in SKIP_NAMES:
                    continue
                if f.startswith(".") and f not in ALLOW_HIDDEN:
                    continue
                full = os.path.join(root, f)
                rel  = os.path.relpath(full, DATA_DIR).replace("\\", "/")
                dest = os.path.join(staging, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    shutil.copy2(full, dest)
                    copied += 1
                except Exception as e:
                    print(f"[sync] stage {rel} error: {e}")

        if copied == 0:
            print("[sync] nothing to backup")
            return

        print(f"[sync] uploading {copied} files to {REPO_ID}...")
        api.upload_folder(
            folder_path=staging,
            repo_id=REPO_ID,
            repo_type="dataset",
            token=HF_TOKEN,
            commit_message="sync: periodic backup",
        )
        print("[sync] backup complete")
        # Save state fingerprint so next cycle skips if nothing changed
        try:
            open(state_file, "w").write(current_state)
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
