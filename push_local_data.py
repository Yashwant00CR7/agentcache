#!/usr/bin/env python3
"""Push local ~/.agentmemory/ to HF dataset repo using upload_folder (single commit)."""
import os, sys, tempfile, shutil

env_path = os.path.expanduser("~/.agentmemory/.env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ[k.strip()] = v

try:
    from huggingface_hub import HfApi
    from huggingface_hub.utils import RepositoryNotFoundError
except ImportError:
    print("[push] huggingface_hub not installed")
    sys.exit(1)

HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO_ID  = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
DATA_DIR = os.path.expanduser("~/.agentmemory")
SKIP_FILES   = {".env"}
ALLOW_HIDDEN = {".hmac"}

if not HF_TOKEN:
    print("[push] No HF_TOKEN in .env")
    sys.exit(1)

api = HfApi(token=HF_TOKEN)

try:
    me = api.whoami()
    print(f"[push] Logged in as: {me['name']}")
except Exception as e:
    print(f"[push] Auth error: {e}")
    sys.exit(1)

# Ensure repo exists
try:
    api.repo_info(REPO_ID, repo_type="dataset", token=HF_TOKEN)
    print(f"[push] Repo {REPO_ID} exists")
except RepositoryNotFoundError:
    print(f"[push] Creating repo {REPO_ID}")
    api.create_repo(REPO_ID, repo_type="dataset", private=True)
except Exception as e:
    print(f"[push] repo_info error: {e}")
    sys.exit(1)

# Build a clean staging dir with only files we want to upload
# (excludes .env, LOCK files, and hidden files not in ALLOW_HIDDEN)
staging = tempfile.mkdtemp(prefix="agentmemory_push_")
print(f"[push] Staging to {staging}")

copied = 0
skipped = 0
for root, dirs, files in os.walk(DATA_DIR):
    is_inside_dolt = ".dolt" in root.replace("\\", "/").split("/")
    if not is_inside_dolt:
        dirs[:] = [d for d in dirs if not d.startswith(".") or d == ".dolt"]
    for fname in files:
        if fname in SKIP_FILES:
            skipped += 1
            continue
        if fname.startswith(".") and fname not in ALLOW_HIDDEN:
            skipped += 1
            continue
        # Skip LOCK files (held open by Dolt)
        if fname == "LOCK":
            skipped += 1
            continue
        full = os.path.join(root, fname)
        rel  = os.path.relpath(full, DATA_DIR).replace("\\", "/")
        dest = os.path.join(staging, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full, dest)
        copied += 1

print(f"[push] {copied} files staged, {skipped} skipped")

try:
    print(f"[push] Uploading to {REPO_ID} (single commit)...")
    api.upload_folder(
        folder_path=staging,
        repo_id=REPO_ID,
        repo_type="dataset",
        token=HF_TOKEN,
        commit_message="sync: push local agentmemory data",
    )
    print(f"[push] Done — all {copied} files uploaded to {REPO_ID}")
except Exception as e:
    print(f"[push] Upload error: {e}")
    sys.exit(1)
finally:
    shutil.rmtree(staging, ignore_errors=True)
