#!/usr/bin/env python3
"""
Sync ~/.agentmemory/ data to/from a private HF Dataset repo.
Usage:
  python3 sync.py restore   -- download DB from HF on startup
  python3 sync.py backup    -- upload DB to HF (called in loop)
"""
import os
import sys
import glob
import shutil

try:
    from huggingface_hub import HfApi, hf_hub_download, list_repo_files
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
except ImportError:
    print("[sync] huggingface_hub not installed, skipping sync")
    sys.exit(0)

HF_TOKEN   = os.environ.get("HF_TOKEN", "")
REPO_ID    = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR   = os.path.expanduser("~/.agentmemory")
SKIP_FILES = {".env"}   # never upload secrets
ALLOW_HIDDEN = {".hmac"}  # hidden files that ARE safe to backup

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
            downloaded = hf_hub_download(
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

    # Ensure repo exists
    try:
        api.repo_info(REPO_ID, repo_type="dataset")
    except RepositoryNotFoundError:
        print(f"[sync] Creating dataset repo {REPO_ID}")
        api.create_repo(REPO_ID, repo_type="dataset", private=True)
    except Exception as e:
        print(f"[sync] repo_info error: {e}")
        return

    # Collect files to upload
    all_files = []
    for root, dirs, files in os.walk(DATA_DIR):
        # Skip hidden directories, EXCEPT if they are inside the '.dolt' folder
        is_inside_dolt = '.dolt' in root.replace('\\', '/').split('/')
        if not is_inside_dolt:
            dirs[:] = [d for d in dirs if not d.startswith('.') or d == '.dolt']
        for f in files:
            if f in SKIP_FILES:
                continue
            if f.startswith('.') and f not in ALLOW_HIDDEN:
                continue
            full = os.path.join(root, f)
            rel  = os.path.relpath(full, DATA_DIR)
            all_files.append((full, rel))

    if not all_files:
        print("[sync] nothing to backup")
        return

    for full_path, rel_path in all_files:
        try:
            api.upload_file(
                path_or_fileobj=full_path,
                path_in_repo=rel_path.replace('\\', '/'),
                repo_id=REPO_ID,
                repo_type="dataset",
                token=HF_TOKEN,
            )
            print(f"[sync] backed up {rel_path}")
        except Exception as e:
            print(f"[sync] backup {rel_path} error: {e}")

    print("[sync] backup complete")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backup"
    if cmd == "restore":
        restore()
    elif cmd == "backup":
        backup()
    else:
        print(f"[sync] unknown command: {cmd}")
        sys.exit(1)
