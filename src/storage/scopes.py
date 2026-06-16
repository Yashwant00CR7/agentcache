"""
src/storage/scopes.py — KV scope registry (A2.3).

Copied from src/functions.py — do NOT delete the original (backward compat).
The KV class defines all storage scope keys used across agentcache-python.
"""


class KV:
    # ---- Folder memory scopes (new) ----

    # Global index of all (folder_path, agent_id) pairs known to the system.
    # Key = "{safe_folder_path}:{agent_id}", value = FolderIndexEntry dict.
    folders = "mem:folders"

    @staticmethod
    def folder_obs(folder_path: str, agent_id: str) -> str:
        """Per-(folder, agent) observations scope.
        Key = obs_id, value = FolderObservation dict.
        """
        safe_path = folder_path.replace("\\", "/").strip("/")
        safe_agent = agent_id.strip()
        return f"mem:folder:{safe_path}:{safe_agent}"

    @staticmethod
    def folder_meta(folder_path: str, agent_id: str) -> str:
        """Per-(folder, agent) metadata scope.
        Key = "meta", value = FolderMeta dict (obsCount, lastUpdated, summary).
        """
        safe_path = folder_path.replace("\\", "/").strip("/")
        safe_agent = agent_id.strip()
        return f"mem:foldermeta:{safe_path}:{safe_agent}"

    @staticmethod
    def obs_dedup(folder_path: str, agent_id: str) -> str:
        """Deduplication index scope for (folder, agent) pairs.
        Key = SHA-256 fingerprint hex of normalized text.
        Value = {"obsId": str, "timestamp": str}
        """
        safe_path = folder_path.replace("\\", "/").strip("/")
        safe_agent = agent_id.strip()
        return f"mem:obs_dedup:{safe_path}:{safe_agent}"

    # ---- Global / shared scopes (kept) ----

    # Long-term memories — unchanged from previous implementation.
    memories = "mem:memories"

    # BM25 index shards — unchanged.
    bm25Index = "mem:index:bm25"

    # Audit log — unchanged.
    audit = "mem:audit"

    # Graph edges — repurposed for folder graph edges.
    relations = "mem:relations"

    # ---- Legacy scopes (read-only; kept for migration and backward compat) ----

    # Legacy session store — read by migrate_sessions_to_folders() and legacy observe().
    sessions = "mem:sessions"

    @staticmethod
    def observations(session_id: str) -> str:
        """Legacy per-session observations scope.
        Key = obs_id, value = raw/synthetic observation dict.
        Read by migrate_sessions_to_folders() and legacy observe().
        """
        return f"mem:obs:{session_id}"

    # Legacy summary / profile / slot / image-ref scopes retained for legacy code paths.
    summaries = "mem:summaries"
    profiles = "mem:profiles"
    slots = "mem:slots"
    imageRefs = "mem:image-refs"

    # Global (cross-project) pinned slots.
    globalSlots = "mem:global-slots"
