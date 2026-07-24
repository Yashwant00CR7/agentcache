"""
ObservationStore — owns folder-scoped observations lifecycle.

Handles ingestion, deduplication, deletion, timeline retrieval,
and startup backfill for folder observations.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..db import StateKV
from ..storage.paths import generate_id
from .kv_scopes import KV
from .search_service import SearchService


@dataclass
class ObservationEvents:
    on_added: List[Callable[[Dict[str, Any]], None]] = field(default_factory=list)
    on_deleted: List[Callable[[List[str]], None]] = field(default_factory=list)
    on_folder_deleted: List[Callable[[str, str], None]] = field(default_factory=list)


def normalize_folder_path(path: str) -> str:
    """Normalize a folder path for safe use in KV scope keys."""
    if not path:
        raise ValueError("folder_path must not be empty")

    path = path[:512]

    raw_parts = path.replace("\\", "/").split("/")
    if any(part == ".." for part in raw_parts):
        raise ValueError(f"folder_path contains path traversal segment '..': {path!r}")

    normalized = os.path.normpath(path).replace("\\", "/").strip("/")

    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"folder_path contains path traversal segment '..': {path!r}")

    if not normalized:
        raise ValueError("folder_path is empty after normalization")

    return normalized


def validate_agent_id(agent_id: str) -> str:
    """Validate and sanitize an agent_id before use in KV scope keys."""
    if not agent_id:
        raise ValueError("agent_id must not be empty")

    agent_id = agent_id.strip()[:512]

    if not agent_id:
        raise ValueError("agent_id must not be empty")

    return agent_id


class ObservationStore:
    """Store for folder-scoped observations."""

    def __init__(
        self,
        kv: StateKV,
        search_service: Optional[SearchService] = None,
        events: Optional[ObservationEvents] = None,
    ) -> None:
        self.kv = kv
        self.search_service = search_service
        self.events = events or ObservationEvents()
        self._dedup_locks: Dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    def _get_dedup_lock(self, folder_path: str, agent_id: str) -> threading.Lock:
        key = f"{folder_path}:{agent_id}"
        with self._locks_mutex:
            if key not in self._dedup_locks:
                self._dedup_locks[key] = threading.Lock()
            return self._dedup_locks[key]

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate, deduplicate, write, index, and fire events for an observation."""
        folder_path_raw = payload.get("folderPath")
        agent_id_raw = payload.get("agentId")
        text_raw = payload.get("text")
        timestamp = payload.get("timestamp")

        if not folder_path_raw:
            raise ValueError("Invalid payload: folderPath is required")
        if not agent_id_raw:
            raise ValueError("Invalid payload: agentId is required")
        if not text_raw:
            raise ValueError("Invalid payload: text is required")
        if not timestamp:
            raise ValueError("Invalid payload: timestamp is required")

        folder_path = normalize_folder_path(folder_path_raw)
        agent_id = validate_agent_id(agent_id_raw)

        from ..legacy import extract_files, infer_type, strip_private_data

        safe_text = strip_private_data(text_raw)[:4000]

        dedup_fp = hashlib.sha256(
            safe_text[:4000].strip().lower().encode("utf-8")
        ).hexdigest()

        dedup_lock = self._get_dedup_lock(folder_path, agent_id)
        with dedup_lock:
            existing_dedup = self.kv.get(KV.obs_dedup(folder_path, agent_id), dedup_fp)
            if (
                existing_dedup
                and isinstance(existing_dedup, dict)
                and existing_dedup.get("obsId")
            ):
                return {"observationId": existing_dedup["obsId"], "deduplicated": True}

            max_obs = int(os.getenv("MAX_OBS_PER_FOLDER", "2000"))
            if max_obs > 0:
                existing_obs = self.kv.list(KV.folder_obs(folder_path, agent_id))
                if len(existing_obs) >= max_obs:
                    raise ValueError(f"Folder observation limit reached ({max_obs})")

            obs_id = generate_id("fobs")

            obs_type = payload.get("type")
            if not obs_type:
                obs_type = infer_type(None, "other")

            title = payload.get("title")
            if not title:
                title = safe_text[:80]

            concepts = payload.get("concepts") or []
            if not isinstance(concepts, list):
                concepts = []

            files = payload.get("files")
            if not isinstance(files, list):
                files = extract_files(payload)

            raw_importance = payload.get("importance")
            if raw_importance is None:
                importance = 5
            else:
                try:
                    importance = max(1, min(10, int(raw_importance)))
                except (TypeError, ValueError):
                    importance = 5

            obs: Dict[str, Any] = {
                "id": obs_id,
                "folderPath": folder_path,
                "agentId": agent_id,
                "timestamp": timestamp,
                "text": safe_text,
                "type": obs_type,
                "title": title,
                "concepts": concepts,
                "files": files,
                "importance": importance,
            }
            if "forgetAfter" in payload:
                obs["forgetAfter"] = payload["forgetAfter"]
            elif (
                payload.get("ttlDays")
                and isinstance(payload["ttlDays"], (int, float))
                and payload["ttlDays"] > 0
            ):
                try:
                    import dateutil.parser

                    ts_dt = dateutil.parser.parse(timestamp)
                    forget_time = ts_dt + datetime.timedelta(days=payload["ttlDays"])
                    obs["forgetAfter"] = forget_time.isoformat().replace("+00:00", "Z")
                except Exception:
                    pass

            self.kv.set(KV.folder_obs(folder_path, agent_id), obs_id, obs)

            self.kv.set(
                KV.obs_lookup,
                obs_id,
                {
                    "folderPath": folder_path,
                    "agentId": agent_id,
                },
            )

            self.kv.set(
                KV.obs_dedup(folder_path, agent_id),
                dedup_fp,
                {"obsId": obs_id, "timestamp": timestamp},
            )

        meta_scope = KV.folder_meta(folder_path, agent_id)
        meta = self.kv.get(meta_scope, "meta") or {
            "folderPath": folder_path,
            "agentId": agent_id,
            "obsCount": 0,
            "lastUpdated": timestamp,
            "summary": None,
        }
        meta["obsCount"] = meta.get("obsCount", 0) + 1
        meta["lastUpdated"] = timestamp
        self.kv.set(meta_scope, "meta", meta)

        index_key = f"{folder_path}:{agent_id}"
        self.kv.set(
            KV.folders,
            index_key,
            {
                "folderPath": folder_path,
                "agentId": agent_id,
                "lastUpdated": meta["lastUpdated"],
                "obsCount": meta["obsCount"],
            },
        )

        if self.search_service:
            try:
                self.search_service.index(obs)
                self.search_service.schedule_persist()
            except Exception as ex:
                print(f"[observation_store] search_service indexing failed: {ex}")

        self.kv.commit_version(f"folder_observe: {obs_id}", agent_id)

        event_payload = {
            "type": "folder_observation",
            "folderPath": folder_path,
            "agentId": agent_id,
            "data": obs,
        }
        if self.events and self.events.on_added:
            for cb in self.events.on_added:
                try:
                    cb(event_payload)
                except Exception as ex:
                    print(f"[observation_store] Error in on_added callback: {ex}")

        return {"observationId": obs_id}

    def dedup(
        self, folder_path_raw: Optional[str] = None, agent_id_raw: Optional[str] = None
    ) -> Dict[str, Any]:
        """Remove duplicate observations for one or all (folder, agent) pairs."""
        if folder_path_raw and agent_id_raw:
            try:
                fp = normalize_folder_path(folder_path_raw)
                aid = validate_agent_id(agent_id_raw)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            pairs = [{"folderPath": fp, "agentId": aid}]
        else:
            pairs = [
                {"folderPath": e.get("folderPath", ""), "agentId": e.get("agentId", "")}
                for e in self.kv.list(KV.folders)
                if e.get("folderPath") and e.get("agentId")
            ]

        total_removed = 0
        total_kept = 0

        for pair in pairs:
            fp = pair["folderPath"]
            aid = pair["agentId"]
            all_obs = self.kv.list(KV.folder_obs(fp, aid))

            fingerprint_map: Dict[str, Dict[str, Any]] = {}
            duplicates: List[str] = []

            for obs in all_obs:
                text = obs.get("text") or ""
                fp_hash = hashlib.sha256(
                    text[:4000].strip().lower().encode("utf-8")
                ).hexdigest()
                if fp_hash not in fingerprint_map:
                    fingerprint_map[fp_hash] = obs
                else:
                    existing_ts = fingerprint_map[fp_hash].get("timestamp", "")
                    this_ts = obs.get("timestamp", "")
                    if this_ts < existing_ts:
                        duplicates.append(fingerprint_map[fp_hash]["id"])
                        fingerprint_map[fp_hash] = obs
                    else:
                        duplicates.append(obs["id"])

            if duplicates:
                self.forget(
                    {"folderPath": fp, "agentId": aid, "observationIds": duplicates}
                )
                total_removed += len(duplicates)

            total_kept += len(fingerprint_map)

            dedup_scope = KV.obs_dedup(fp, aid)
            for fp_hash, obs in fingerprint_map.items():
                self.kv.set(
                    dedup_scope,
                    fp_hash,
                    {"obsId": obs["id"], "timestamp": obs.get("timestamp", "")},
                )

        return {
            "success": True,
            "deduplicated": total_removed,
            "pairs_processed": len(pairs),
            "kept": total_kept,
        }

    def forget(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a folder pair, specific observations, or a global memory."""
        memory_id = data.get("memoryId")
        session_id = data.get("sessionId")
        folder_path_raw = data.get("folderPath")
        agent_id_raw = data.get("agentId")
        obs_ids = data.get("observationIds") or []
        deleted = 0
        deleted_mem_ids: List[str] = []
        deleted_obs_ids: List[str] = []

        if memory_id:
            mem = self.kv.get(KV.memories, memory_id)
            self.kv.delete(KV.memories, memory_id)
            if mem and isinstance(mem, dict) and mem.get("imageRef"):
                ref = mem["imageRef"]
                refs = self.kv.get(KV.imageRefs, ref) or 0
                if refs > 0:
                    self.kv.set(KV.imageRefs, ref, refs - 1)
            if self.search_service:
                self.search_service.remove(memory_id)
            deleted_mem_ids.append(memory_id)
            deleted += 1

        if folder_path_raw and agent_id_raw:
            try:
                fp = normalize_folder_path(folder_path_raw)
                aid = validate_agent_id(agent_id_raw)
            except ValueError as exc:
                return {"success": False, "error": str(exc), "deleted": 0}

            obs_scope = KV.folder_obs(fp, aid)
            meta_scope = KV.folder_meta(fp, aid)
            index_key = f"{fp}:{aid}"
            if "observationIds" in data and data["observationIds"] is not None:
                partial_deleted = 0
                for oid in obs_ids:
                    obs = self.kv.get(obs_scope, oid)
                    existed = self.kv.delete(obs_scope, oid)
                    if existed:
                        self.kv.delete(KV.obs_lookup, oid)
                        if self.search_service:
                            self.search_service.remove(oid)
                        if obs and isinstance(obs, dict) and obs.get("text"):
                            fp_text = obs["text"][:4000]
                            dedup_fp = hashlib.sha256(
                                fp_text.strip().lower().encode("utf-8")
                            ).hexdigest()
                            self.kv.delete(KV.obs_dedup(fp, aid), dedup_fp)
                        deleted_obs_ids.append(oid)
                        partial_deleted += 1
                        deleted += 1

                if partial_deleted > 0:
                    meta = self.kv.get(meta_scope, "meta")
                    if meta and isinstance(meta, dict):
                        current_count = meta.get("obsCount", 0)
                        meta["obsCount"] = max(0, current_count - partial_deleted)
                        self.kv.set(meta_scope, "meta", meta)
                        index_entry = self.kv.get(KV.folders, index_key)
                        if index_entry and isinstance(index_entry, dict):
                            index_entry["obsCount"] = meta["obsCount"]
                            self.kv.set(KV.folders, index_key, index_entry)

                if deleted_obs_ids and self.events and self.events.on_deleted:
                    for cb in self.events.on_deleted:
                        try:
                            cb(deleted_obs_ids)
                        except Exception as ex:
                            print(
                                f"[observation_store] Error in on_deleted callback: {ex}"
                            )
            else:
                all_obs = self.kv.list(obs_scope)
                for obs in all_obs:
                    obs_id = obs.get("id")
                    if obs_id:
                        self.kv.delete(obs_scope, obs_id)
                        self.kv.delete(KV.obs_lookup, obs_id)
                        if self.search_service:
                            self.search_service.remove(obs_id)
                        deleted_obs_ids.append(obs_id)
                        deleted += 1

                self.kv.delete(meta_scope, "meta")
                self.kv.delete(KV.folders, index_key)

                dedup_scope = KV.obs_dedup(fp, aid)
                for item in self.kv.list(dedup_scope):
                    if isinstance(item, dict) and item.get("id"):
                        self.kv.delete(dedup_scope, item["id"])

                if self.events and self.events.on_folder_deleted:
                    for cb in self.events.on_folder_deleted:
                        try:
                            cb(fp, aid)
                        except Exception as ex:
                            print(
                                f"[observation_store] Error in on_folder_deleted callback: {ex}"
                            )

        if session_id and obs_ids:
            for oid in obs_ids:
                base_oid = oid.replace(":raw", "")
                obs = self.kv.get(KV.observations(session_id), base_oid)
                raw_obs = self.kv.get(KV.observations(session_id), f"{base_oid}:raw")

                self.kv.delete(KV.observations(session_id), base_oid)
                self.kv.delete(KV.observations(session_id), f"{base_oid}:raw")
                self.kv.delete(KV.obs_lookup, base_oid)

                for o in (obs, raw_obs):
                    if o and isinstance(o, dict):
                        img = o.get("imageData") or o.get("imageRef")
                        if img:
                            refs = self.kv.get(KV.imageRefs, img) or 0
                            if refs > 0:
                                self.kv.set(KV.imageRefs, img, refs - 1)

                if self.search_service:
                    self.search_service.remove(base_oid)
                    self.search_service.remove(f"{base_oid}:raw")
                deleted_obs_ids.append(oid)
                deleted += 1

        if session_id and not obs_ids and not memory_id and not folder_path_raw:
            obs_list = self.kv.list(KV.observations(session_id))
            for obs in obs_list:
                self.kv.delete(KV.observations(session_id), obs["id"])
                self.kv.delete(KV.obs_lookup, obs["id"])
                if isinstance(obs, dict):
                    img = obs.get("imageData") or obs.get("imageRef")
                    if img:
                        refs = self.kv.get(KV.imageRefs, img) or 0
                        if refs > 0:
                            self.kv.set(KV.imageRefs, img, refs - 1)
                if self.search_service:
                    self.search_service.remove(obs["id"])
                deleted_obs_ids.append(obs["id"])
                deleted += 1
            self.kv.delete(KV.sessions, session_id)
            self.kv.delete(KV.summaries, session_id)
            deleted += 2

        if deleted > 0 and self.search_service:
            self.search_service.schedule_persist()

        return {"success": True, "deleted": deleted}

    def timeline(
        self,
        limit: int = 100,
        folder_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return observations sorted by timestamp descending."""
        index_entries = self.kv.list(KV.folders)

        if folder_path is not None:
            index_entries = [
                e for e in index_entries if e.get("folderPath") == folder_path
            ]

        if agent_id is not None:
            index_entries = [e for e in index_entries if e.get("agentId") == agent_id]

        all_obs: List[Dict[str, Any]] = []

        for entry in index_entries:
            fp = entry.get("folderPath", "")
            aid = entry.get("agentId", "")
            if not fp or not aid:
                continue

            obs_scope = KV.folder_obs(fp, aid)
            obs_list = self.kv.list(obs_scope)

            if before is not None:
                obs_list = [o for o in obs_list if o.get("timestamp", "") < before]

            if after is not None:
                obs_list = [o for o in obs_list if o.get("timestamp", "") > after]

            all_obs.extend(obs_list)

        all_obs.sort(key=lambda o: o.get("timestamp", ""), reverse=True)
        return all_obs[:limit]

    def backfill_lookup(self) -> None:
        """Ensure every folder observation has an entry in KV.obs_lookup."""
        folders = self.kv.list(KV.folders)
        if not folders:
            return

        for entry in folders:
            fp = entry.get("folderPath")
            aid = entry.get("agentId")
            if not fp or not aid:
                continue
            obs_list = self.kv.list(KV.folder_obs(fp, aid))
            for obs in obs_list:
                oid = obs.get("id")
                if oid and not self.kv.get(KV.obs_lookup, oid):
                    self.kv.set(KV.obs_lookup, oid, {"folderPath": fp, "agentId": aid})

    def rebuild_index(self) -> int:
        """Clear and rebuild the search index from all stored observations."""
        if self.search_service:
            self.search_service.bm25.clear()
            if self.search_service.vector:
                self.search_service.vector.clear()

        total_indexed = 0

        # Folder-based observations
        folder_pairs = self.kv.list(KV.folders)
        for entry in folder_pairs:
            fp = entry.get("folderPath")
            aid = entry.get("agentId")
            if not fp or not aid:
                continue
            obs_list = self.kv.list(KV.folder_obs(fp, aid))
            for obs in obs_list:
                oid = obs.get("id")
                if not oid:
                    continue
                self.kv.set(KV.obs_lookup, oid, {"folderPath": fp, "agentId": aid})
                if self.search_service:
                    self.search_service.index(obs)
                total_indexed += 1

        # Global memories
        memories = self.kv.list(KV.memories)
        for mem in memories:
            if mem.get("isLatest") is False:
                continue
            if not mem.get("title") or not mem.get("content"):
                continue
            converted = {
                "id": mem["id"],
                "sessionId": "memory",
                "timestamp": mem.get("createdAt", ""),
                "title": mem["title"],
                "text": mem["content"],
                "type": mem.get("type", "fact"),
                "agentId": mem.get("agentId", ""),
            }
            if self.search_service:
                self.search_service.index(converted)
            total_indexed += 1

        if self.search_service and total_indexed > 0:
            self.search_service.schedule_persist()

        return total_indexed
