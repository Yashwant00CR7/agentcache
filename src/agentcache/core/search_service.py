"""
SearchService — owns BM25 + vector index management and querying.

Single seam for all search index operations. Callers (ObservationStore,
MemoryStore, routes) call this service instead of touching index globals.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from ..search import HybridSearch, SearchIndex, VectorIndex
from ..storage.paths import generate_id
from .kv_scopes import KV


class IndexPersistence:
    """Persist BM25 and vector indexes to the KV store with a debounce queue."""

    DEBOUNCE_SECONDS: float = 5.0

    def __init__(
        self, kv: Any, bm25: SearchIndex, vector: Optional[VectorIndex] = None
    ):
        self.kv = kv
        self.bm25 = bm25
        self.vector = vector
        self._timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()

    def schedule_save(self) -> None:
        """Schedule a debounced save — resets the 5-second timer on each call."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.DEBOUNCE_SECONDS, self._fire_save)
            self._timer.daemon = True
            self._timer.start()

    def _fire_save(self) -> None:
        """Called by the timer after DEBOUNCE_SECONDS of inactivity."""
        with self._timer_lock:
            self._timer = None
        self.save()

    def flush(self) -> None:
        """Cancel any pending debounce timer and save immediately (used on shutdown)."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self.save()

    def save(self) -> None:
        try:
            bm25_dirty = getattr(self.bm25, "_dirty", True)
            vector_dirty = self.vector and getattr(self.vector, "_dirty", True)

            if bm25_dirty:
                self.save_sharded_index(
                    json.dumps(self.bm25.serialize_data()),
                    "data:manifest",
                    "data",
                    "mem:index:bm25:bm25:",
                )
                self.bm25._dirty = False

            if self.vector and vector_dirty:
                self.save_sharded_index(
                    json.dumps(self.vector.serialize_data()),
                    "vectors:manifest",
                    "vectors",
                    "mem:index:bm25:vectors:",
                )
                self.vector._dirty = False

            if not bm25_dirty and not vector_dirty:
                print("[index persistence] indexes not dirty — skipping save")
        except Exception as e:
            print(f"[index persistence] failed to save index: {e}")

    def save_sharded_index(
        self, serialized: str, manifest_key: str, legacy_key: str, scope_prefix: str
    ) -> None:
        previous = self.kv.get(KV.bm25Index, manifest_key)
        generation = generate_id("idx")
        chunk_chars = 2000000
        shards = []
        chunks = []

        offset = 0
        shard_idx = 0
        while offset < len(serialized):
            scope = f"{scope_prefix}{generation}:{str(shard_idx).zfill(5)}"
            chunk = serialized[offset : offset + chunk_chars]
            shards.append({"scope": scope, "key": "data", "chars": len(chunk)})
            chunks.append(chunk)
            offset += chunk_chars
            shard_idx += 1

        for shard, chunk in zip(shards, chunks):
            self.kv.set(shard["scope"], shard["key"], chunk)

        next_manifest = {
            "v": 1,
            "generation": generation,
            "shards": shards,
            "chars": len(serialized),
        }

        self.kv.set(KV.bm25Index, manifest_key, next_manifest)
        self.kv.delete(KV.bm25Index, legacy_key)

        # Cleanup obsolete shards
        if hasattr(self.kv, "_lock") and hasattr(self.kv, "_get_conn"):
            with self.kv._lock:
                max_retries = 5
                delay = 0.05
                for attempt in range(max_retries):
                    try:
                        conn = self.kv._get_conn()
                        cursor = conn.cursor()
                        try:
                            cursor.execute(
                                "SELECT DISTINCT scope FROM kv_store WHERE scope LIKE ?",
                                (scope_prefix + "%",),
                            )
                            rows = cursor.fetchall()
                            current_scopes = {s["scope"] for s in shards}
                            to_delete = [
                                row["scope"]
                                for row in rows
                                if row["scope"] not in current_scopes
                            ]
                            if to_delete:
                                for i in range(0, len(to_delete), 50):
                                    chunk_delete = to_delete[i : i + 50]
                                    format_strings = ",".join(["?"] * len(chunk_delete))
                                    cursor.execute(
                                        f"DELETE FROM kv_store WHERE scope IN ({format_strings})",
                                        tuple(chunk_delete),
                                    )
                                    conn.commit()
                            break
                        finally:
                            cursor.close()
                    except sqlite3.OperationalError as ex:
                        err_msg = str(ex).lower()
                        if (
                            "locked" in err_msg or "busy" in err_msg
                        ) and attempt < max_retries - 1:
                            time.sleep(delay)
                            delay *= 2
                            continue
                        print(
                            f"[index persistence] error cleaning up obsolete shards: {ex}"
                        )
                        break
                    except Exception as ex:
                        print(
                            f"[index persistence] error cleaning up obsolete shards: {ex}"
                        )
                        break

        if (
            previous
            and isinstance(previous, dict)
            and previous.get("v") == 1
            and isinstance(previous.get("shards"), list)
        ):
            current_shards = {(s["scope"], s["key"]) for s in shards}
            for old_shard in previous["shards"]:
                if (old_shard["scope"], old_shard["key"]) not in current_shards:
                    self.kv.delete(old_shard["scope"], old_shard["key"])

    def load(self) -> Dict[str, Any]:
        bm25_data = self.load_sharded_data("data", "data:manifest")
        bm25_loaded = False
        if bm25_data:
            try:
                self.bm25.restore_from_data(json.loads(bm25_data))
                bm25_loaded = True
            except Exception as e:
                print(f"[index persistence] failed to restore BM25: {e}")

        vector_loaded = False
        if self.vector:
            vector_data = self.load_sharded_data("vectors", "vectors:manifest")
            if vector_data:
                try:
                    self.vector.restore_from_data(json.loads(vector_data))
                    vector_loaded = True
                except Exception as e:
                    print(f"[index persistence] failed to restore vectors: {e}")

        return {"bm25": bm25_loaded, "vector": vector_loaded}

    def load_sharded_data(self, legacy_key: str, manifest_key: str) -> Optional[str]:
        manifest = self.kv.get(KV.bm25Index, manifest_key)
        if manifest and isinstance(manifest, dict) and manifest.get("v") == 1:
            shards = manifest.get("shards", [])
            chunks = []
            for shard in shards:
                chunk = self.kv.get(shard["scope"], shard["key"])
                if chunk is None:
                    print(f"[index persistence] missing shard {shard['scope']}")
                    return None
                chunks.append(chunk)
            return "".join(chunks)

        legacy = self.kv.get(KV.bm25Index, legacy_key)
        if isinstance(legacy, str):
            return legacy
        return None


class SearchService:
    """Owns the BM25 index, vector index, and hybrid search.

    Dependencies are injected via the constructor so tests can pass
    lightweight fakes without any Flask or SQLite machinery.

    Public interface
    ----------------
    index(obs)                            — add/replace an observation in both indexes
    remove(obs_id)                        — remove from both indexes
    search(query, limit, folder_path, ...) — hydrated hybrid search results
    schedule_persist()                    — debounce-schedule an index save
    flush_persist()                       — flush immediately (used on shutdown)
    """

    def __init__(
        self,
        bm25_index: Optional[SearchIndex] = None,
        vector_index: Optional[VectorIndex] = None,
        embedding_provider: Any = None,
        persistence: Any = None,
        kv: Any = None,
        # Backward-compatibility alias parameters
        bm25: Optional[SearchIndex] = None,
        vector: Optional[VectorIndex] = None,
    ) -> None:
        actual_bm25 = bm25_index if bm25_index is not None else bm25
        if actual_bm25 is None:
            actual_bm25 = SearchIndex()
        actual_vector = vector_index if vector_index is not None else vector

        actual_kv = kv
        actual_persistence = None
        if isinstance(persistence, IndexPersistence):
            actual_persistence = persistence
        elif persistence is not None:
            # 4th positional parameter passed was kv
            actual_kv = persistence

        self.bm25 = actual_bm25
        self.vector = actual_vector
        self.embedding_provider = embedding_provider
        self._kv = actual_kv

        self.hybrid = HybridSearch(
            self.bm25,
            self.vector if self.vector else None,
            self.embedding_provider,
            self._kv,
        )

        if actual_persistence is not None:
            self._persistence: Optional[IndexPersistence] = actual_persistence
        elif self._kv is not None:
            self._persistence = IndexPersistence(self._kv, self.bm25, self.vector)
        else:
            self._persistence = None

    # ------------------------------------------------------------------
    # Index mutations
    # ------------------------------------------------------------------

    def index(self, obs: Dict[str, Any]) -> None:
        """Add an observation to BM25 and (if available) vector indexes."""
        self.bm25.add(obs)
        if self.vector and self.embedding_provider:
            obs_id = obs.get("id", "")
            session_id = obs.get("sessionId") or obs.get("folderPath") or "unknown"
            title = obs.get("title", "")
            text = obs.get("text") or obs.get("narrative") or ""
            combined = (title + " " + text).strip()[:16000]
            try:
                embedding = self.embedding_provider.embed(combined)
                if len(embedding) == self.embedding_provider.dimensions:
                    self.vector.add(obs_id, session_id, embedding)
            except Exception as e:
                print(f"[search_service] vector embed failed for {obs_id}: {e}")

    def remove(self, obs_id: str) -> None:
        """Remove an observation from both BM25 and vector indexes."""
        self.bm25.remove(obs_id)
        if self.vector:
            self.vector.remove(obs_id)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 20,
        folder_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        kv: Any = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25 + vector search. Returns hydrated result dicts.

        Results include all fields from the stored observation plus a
        ``score`` key. Filtered by ``folder_path`` and ``agent_id``
        when provided.
        """
        if not query or not query.strip():
            return []

        active_kv = kv if kv is not None else self._kv

        candidates = self.hybrid.search(query, limit * 2)

        results: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for candidate in candidates:
            obs_id = candidate.get("obsId") or candidate.get("id", "")
            score = candidate.get("combinedScore") or candidate.get("score", 0.0)

            if not obs_id or obs_id in seen_ids:
                continue

            if active_kv is not None:
                # 1. Try O(1) lookup index first
                lookup = active_kv.get(KV.obs_lookup, obs_id)
                if lookup and isinstance(lookup, dict):
                    fp = lookup.get("folderPath")
                    aid = lookup.get("agentId")
                    if fp and aid:
                        if folder_path is not None and fp != folder_path:
                            continue
                        if agent_id is not None and aid != agent_id:
                            continue
                        obs = active_kv.get(KV.folder_obs(fp, aid), obs_id)
                        if obs and isinstance(obs, dict):
                            result = dict(obs)
                            result["score"] = score
                            result.setdefault("folderPath", fp)
                            result.setdefault("agentId", aid)
                            results.append(result)
                            seen_ids.add(obs_id)
                            continue

                # 2. Fallback scan for unindexed folder observations
                if obs_id.startswith("fobs_"):
                    found = False
                    for entry in active_kv.list(KV.folders):
                        fp = entry.get("folderPath", "")
                        aid = entry.get("agentId", "")
                        if not fp or not aid:
                            continue
                        if folder_path is not None and fp != folder_path:
                            continue
                        if agent_id is not None and aid != agent_id:
                            continue
                        obs = active_kv.get(KV.folder_obs(fp, aid), obs_id)
                        if obs and isinstance(obs, dict):
                            result = dict(obs)
                            result["score"] = score
                            result.setdefault("folderPath", fp)
                            result.setdefault("agentId", aid)
                            results.append(result)
                            seen_ids.add(obs_id)
                            # Lazy backfill lookup
                            active_kv.set(
                                KV.obs_lookup,
                                obs_id,
                                {"folderPath": fp, "agentId": aid},
                            )
                            found = True
                            break
                    if found:
                        continue

                # 3. Try global memories
                mem = active_kv.get(KV.memories, obs_id)
                if mem and isinstance(mem, dict):
                    if mem.get("isLatest") is not False:
                        result = dict(mem)
                        result["score"] = score
                        result.setdefault("folderPath", "")
                        result.setdefault("agentId", mem.get("agentId") or "")
                        results.append(result)
                        seen_ids.add(obs_id)
            else:
                # If no KV available for hydration, return raw candidate dict with score
                result = dict(candidate)
                result["score"] = score
                results.append(result)
                seen_ids.add(obs_id)

        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def schedule_persist(self) -> None:
        """Debounce-schedule an index persistence save."""
        if self._persistence:
            self._persistence.schedule_save()

    def flush_persist(self) -> None:
        """Flush the debounce timer and save immediately (used on shutdown)."""
        if self._persistence:
            self._persistence.flush()

    def load_persisted(self) -> Dict[str, Any]:
        """Load previously saved indexes from the KV store. Called on startup."""
        if self._persistence:
            return self._persistence.load()
        return {"bm25": False, "vector": False}

    # ------------------------------------------------------------------
    # Index size (used by health check and index-sync verification)
    # ------------------------------------------------------------------

    @property
    def bm25_size(self) -> int:
        return self.bm25.size

    @property
    def vector_size(self) -> int:
        return self.vector.size if self.vector else 0
