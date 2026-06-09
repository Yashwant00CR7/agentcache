#!/usr/bin/env python3
"""Upload second-brain markdown files to HF dataset repo."""
import os, sys

env_path = os.path.expanduser("~/.agentmemory/.env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ[k.strip()] = v

from huggingface_hub import HfApi

HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO_ID  = os.environ.get("AGENTMEMORY_DATASET_REPO", "Yash030/agentmemory-python-data")
SRC_DIR  = r"D:\Downloads\Projects\Other Projects\Know about me\second-brain"

api = HfApi(token=HF_TOKEN)
me = api.whoami()
print(f"[push] Logged in as: {me['name']}")

files = [f for f in os.listdir(SRC_DIR) if f.endswith(".md")]
print(f"[push] Uploading {len(files)} files to {REPO_ID}/second-brain/")

for fname in files:
    full = os.path.join(SRC_DIR, fname)
    api.upload_file(
        path_or_fileobj=full,
        path_in_repo=f"second-brain/{fname}",
        repo_id=REPO_ID,
        repo_type="dataset",
        token=HF_TOKEN,
    )
    print(f"[push] OK  second-brain/{fname}")

print(f"[push] Done — {len(files)} files uploaded")
