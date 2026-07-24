"""
KV scope key registry.

Single source of truth for every SQLite scope string used in the system.
Import this module wherever a KV scope key is needed — routes, stores, workers.
"""


class KV:
    # ---- Folder memory scopes ----

    # Global index of all (folder_path, agent_id) pairs known to the system.
    # Key = "{safe_folder_path}:{agent_id}", value = FolderIndexEntry dict.
    folders = "mem:folders"

    # Lookup index for O(1) observation hydration.
    # Scope = "mem:obs_lookup", Key = obs_id, Value = {"folderPath": ..., "agentId": ...}
    obs_lookup = "mem:obs_lookup"

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

    # ---- Global / shared scopes ----

    # Long-term memories.
    memories = "mem:memories"

    # BM25 index shards.
    bm25Index = "mem:index:bm25"

    # Audit log.
    audit = "mem:audit"

    # Graph edges.
    relations = "mem:relations"

    # ---- Legacy scopes (read-only; kept for migration and backward compat) ----

    # Legacy session store.
    sessions = "mem:sessions"

    @staticmethod
    def observations(session_id: str) -> str:
        """Legacy per-session observations scope."""
        return f"mem:obs:{session_id}"

    # Lessons — confidence-scored learning entries.
    lessons = "mem:lessons"

    # Legacy summary / profile / slot / image-ref scopes.
    summaries = "mem:summaries"
    profiles = "mem:profiles"
    slots = "mem:slots"
    imageRefs = "mem:image-refs"

    # Global (cross-project) pinned slots.
    globalSlots = "mem:global-slots"

    # Semantic and procedural memory scopes (used by consolidate).
    semantic = "mem:semantic"
    procedural = "mem:procedural"
