import json
import os
import sys
import urllib.parse

from .db import StateKV


def import_old_data(old_db_path: str, kv: StateKV) -> bool:
    if not os.path.exists(old_db_path):
        print(f"[import] Error: Path {old_db_path} does not exist.")
        return False

    print(f"[import] Starting migration from: {old_db_path}")
    count = 0

    for filename in os.listdir(old_db_path):
        if not filename.endswith(".bin"):
            continue
        if not filename.startswith("mem%3A"):
            continue

        # URL decode the scope name (e.g. mem%3Asessions.bin -> mem:sessions)
        decoded_name = urllib.parse.unquote(filename)
        scope = decoded_name[:-4] if decoded_name.endswith(".bin") else decoded_name

        filepath = os.path.join(old_db_path, filename)
        try:
            with open(filepath, "rb") as f:
                data = f.read()

            if not data:
                continue

            # Find the last matching JSON curly brace to strip binary padding/footers
            try:
                last_brace = data.rindex(b"}")
                json_str = data[: last_brace + 1].decode("utf-8")
                records = json.loads(json_str)
            except Exception as e:
                print(f"[import] Skipping {filename}: could not parse JSON ({e})")
                continue

            print(f"[import] Scope '{scope}': found {len(records)} items. Migrating...")

            for key, val in records.items():
                kv.set(scope, key, val)
                count += 1

        except Exception as e:
            print(f"[import] Failed to process {filename}: {e}")

    if count > 0:
        print("[import] Creating Dolt version commit...")
        kv.commit_version(
            "Import legacy AgentCache database from Hugging Face", "system"
        )

    print(f"[import] Finished! Migrated {count} total records into Dolt SQL.")
    return True


if __name__ == "__main__":
    # Prioritize the restored HF data in the user's home folder, falling back to local workspace data
    home_path = os.path.expanduser(os.path.join("~", ".agentcache", "state_store.db"))
    workspace_path = r"d:\Downloads\Projects\Other Projects\agentcache\agentcache\data\state_store.db"

    if os.path.exists(home_path):
        default_path = home_path
        print(f"[import] Found restored HF data at: {default_path}")
    else:
        default_path = workspace_path
        print(
            f"[import] HF data not found at home path, falling back to workspace: {default_path}"
        )

    # Allow custom path via command line argument
    if len(sys.argv) > 1:
        default_path = sys.argv[1]
        print(f"[import] Overriding path with CLI argument: {default_path}")

    # Initialize connection
    print("[import] Initializing Dolt StateKV...")
    kv = StateKV()

    import_old_data(default_path, kv)
