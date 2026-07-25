"""Project profile, data export, migration, relations, auto-forget, health check."""

import datetime
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Set

from ..db import StateKV
from ..storage.paths import generate_id
from .audit_log import safe_audit
from .config import commit_if_enabled, get_agent_id, is_agent_scope_isolated
from .kv_scopes import KV


def get_project_profile(kv: StateKV, project: str) -> Dict[str, Any]:
    prof = kv.get(KV.profiles, project)
    if not prof:
        prof = {
            "project": project,
            "topConcepts": [],
            "topFiles": [],
            "conventions": [],
            "commonErrors": [],
            "updatedAt": datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    if not prof.get("topConcepts") and not prof.get("topFiles"):
        prof = build_project_profile(kv, project)
    return prof


def build_project_profile(kv: StateKV, project: str) -> Dict[str, Any]:
    import os.path as _osp
    import re as _re
    from collections import Counter

    prof = kv.get(KV.profiles, project)
    if not prof:
        prof = {
            "project": project,
            "topConcepts": [],
            "topFiles": [],
            "conventions": [],
            "commonErrors": [],
            "updatedAt": datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    if not prof.get("topConcepts") and not prof.get("topFiles"):
        sessions = kv.list(KV.sessions)
        project_sessions = [s for s in sessions if s.get("project") == project]
        concept_counts = Counter()
        file_counts = Counter()

        def _harvest_file(path, fc, cc):
            if not isinstance(path, str) or not path:
                return
            fc[path] += 1
            parts = _re.split(r"[\\/]", path)
            fname = parts[-1] if parts else ""
            skip = {"tmp", "temp", "claude", "appdata", "local", "users", "windows"}
            for part in parts[:-1]:
                p = part.lower().strip()
                if (
                    p
                    and len(p) > 2
                    and p not in skip
                    and not _re.match(r"^[a-z]:|^\.|^--", p)
                ):
                    cc[p] += 1
            stem = _osp.splitext(fname)[0]
            if stem and len(stem) > 2:
                cc[stem.lower()] += 1
            ext = _osp.splitext(fname)[1].lstrip(".")
            if ext in ("py", "ts", "js", "jsx", "tsx", "go", "rs", "java", "cs", "cpp"):
                cc[ext] += 1

        for s in project_sessions:
            sid = s.get("id", "")
            if not sid:
                continue
            for o in kv.list(KV.observations(sid)):
                for c in o.get("concepts") or []:
                    if isinstance(c, str) and c:
                        concept_counts[c] += 1
                for f in o.get("files") or []:
                    _harvest_file(f, file_counts, concept_counts)
                tn = o.get("toolName")
                if tn:
                    concept_counts[tn] += 1
                ti = o.get("toolInput")
                if isinstance(ti, str):
                    try:
                        ti = json.loads(ti)
                    except Exception:
                        ti = {}
                if isinstance(ti, dict):
                    for fk in ("path", "file_path", "file", "filename"):
                        _harvest_file(ti.get(fk, ""), file_counts, concept_counts)
                narr = o.get("narrative") or o.get("raw") or ""
                if isinstance(narr, str) and narr.startswith("{"):
                    try:
                        nd = json.loads(narr)
                        if isinstance(nd, dict):
                            tn2 = nd.get("toolName") or nd.get("tool_name")
                            if tn2:
                                concept_counts[tn2] += 1
                            for fk in ("path", "file_path", "file", "filename"):
                                _harvest_file(
                                    nd.get(fk, ""), file_counts, concept_counts
                                )
                    except Exception:
                        pass

        for m in kv.list(KV.memories):
            if m.get("project") == project:
                for c in m.get("concepts") or []:
                    if c:
                        concept_counts[c] += 1
                for f in m.get("files") or []:
                    _harvest_file(f, file_counts, concept_counts)

        prof["topConcepts"] = [
            {"concept": c, "frequency": n} for c, n in concept_counts.most_common(20)
        ]
        prof["topFiles"] = [
            {"file": f, "frequency": n} for f, n in file_counts.most_common(20)
        ]
        prof["sessionCount"] = len(project_sessions)

    return prof


def set_project_profile(
    kv: StateKV, project: str, profile: Dict[str, Any]
) -> Dict[str, Any]:
    profile["updatedAt"] = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    kv.set(KV.profiles, project, profile)

    commit_if_enabled(kv, f"Set project profile for {project}", get_agent_id())

    return profile


def export_data(kv: StateKV, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if data is None:
        data = {}

    exported_at = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )

    isolated = is_agent_scope_isolated()
    isolated_agent_id = get_agent_id()

    folder_pairs = kv.list(KV.folders)
    folders_export = []
    for entry in folder_pairs:
        fp = entry.get("folderPath")
        aid = entry.get("agentId")
        if not fp or not aid:
            continue
        if isolated and isolated_agent_id and aid != isolated_agent_id:
            continue

        meta = kv.get(KV.folder_meta(fp, aid), "meta") or {
            "folderPath": fp,
            "agentId": aid,
            "lastUpdated": entry.get("lastUpdated", ""),
            "obsCount": entry.get("obsCount", 0),
        }
        observations = kv.list(KV.folder_obs(fp, aid))
        folders_export.append(
            {
                "folderPath": fp,
                "agentId": aid,
                "meta": meta,
                "observations": observations,
            }
        )

    memories = kv.list(KV.memories)
    if isolated and isolated_agent_id:
        memories = [m for m in memories if m.get("agentId") == isolated_agent_id]

    return {
        "folders": folders_export,
        "memories": memories,
        "exportedAt": exported_at,
        "version": "2.0",
    }


def migrate_sessions_to_folders(kv: StateKV, dry_run: bool = False) -> Dict[str, Any]:
    """Migrate legacy session-based observations to folder-based storage.
    Non-destructive: old mem:sessions / mem:obs:* scopes are never deleted.
    """
    from .observation_store import normalize_folder_path

    _MAX_PATH_LEN = 512

    sessions = kv.list(KV.sessions)
    migrated_sessions = 0
    migrated_observations = 0
    errors = []

    for session in sessions:
        session_id = session.get("id")
        if not session_id:
            continue
        try:
            fp_raw = session.get("cwd") or session.get("project") or "unknown"
            aid = (session.get("agentId") or "unknown").strip()[:_MAX_PATH_LEN]
            try:
                fp = normalize_folder_path(fp_raw)
            except ValueError:
                fp = "unknown"

            obs_list = kv.list(KV.observations(session_id))
            session_obs_count = 0
            for obs in obs_list:
                obs_id = obs.get("id", "")
                if obs_id.endswith(":raw"):
                    continue
                folder_obs = {
                    "id": obs_id,
                    "folderPath": fp,
                    "agentId": aid,
                    "timestamp": obs.get("timestamp", ""),
                    "text": obs.get("narrative")
                    or obs.get("raw")
                    or obs.get("title")
                    or "",
                    "type": obs.get("type", "other"),
                    "title": obs.get("title", ""),
                    "concepts": obs.get("concepts") or [],
                    "files": obs.get("files") or [],
                    "importance": obs.get("importance", 5),
                }
                if isinstance(folder_obs["text"], dict):
                    folder_obs["text"] = json.dumps(folder_obs["text"])[:4000]
                folder_obs["text"] = str(folder_obs["text"])[:4000]

                if not dry_run:
                    kv.set(KV.folder_obs(fp, aid), obs_id, folder_obs)
                    kv.set(
                        KV.obs_lookup,
                        obs_id,
                        {
                            "folderPath": fp,
                            "agentId": aid,
                        },
                    )
                session_obs_count += 1
                migrated_observations += 1

            if not dry_run and session_obs_count > 0:
                meta_scope = KV.folder_meta(fp, aid)
                meta = kv.get(meta_scope, "meta") or {
                    "folderPath": fp,
                    "agentId": aid,
                    "obsCount": 0,
                    "lastUpdated": session.get("updatedAt", ""),
                    "summary": None,
                }
                meta["obsCount"] = meta.get("obsCount", 0) + session_obs_count
                meta["lastUpdated"] = (
                    session.get("updatedAt", "") or meta["lastUpdated"]
                )
                kv.set(meta_scope, "meta", meta)

                index_key = f"{fp}:{aid}"
                kv.set(
                    KV.folders,
                    index_key,
                    {
                        "folderPath": fp,
                        "agentId": aid,
                        "lastUpdated": meta["lastUpdated"],
                        "obsCount": meta["obsCount"],
                    },
                )

            migrated_sessions += 1
        except Exception as e:
            errors.append({"sessionId": session_id, "error": str(e)})

    return {
        "migrated_sessions": migrated_sessions,
        "migrated_observations": migrated_observations,
        "errors": errors,
        "dry_run": dry_run,
    }


def get_relations(kv: StateKV) -> List[Dict[str, Any]]:
    return kv.list(KV.relations)


def add_relation(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    rel = {
        "id": generate_id("rel"),
        "sourceId": data["sourceId"],
        "targetId": data["targetId"],
        "type": data["type"],
        "createdAt": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    kv.set(KV.relations, rel["id"], rel)

    agent_id = data.get("agentId") or get_agent_id()
    commit_if_enabled(
        kv,
        f"Add relation {rel['type']} between {rel['sourceId']} and {rel['targetId']}",
        agent_id,
    )

    return rel


def auto_forget(kv: StateKV, dry_run: bool = False) -> Dict[str, Any]:
    from .. import legacy as _legacy

    now_dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    evicted_memories = []
    evicted_observations = []
    evicted_folder_observations = []

    memories = kv.list(KV.memories)
    for mem in memories:
        forget_after = mem.get("forgetAfter")
        if forget_after:
            try:
                import dateutil.parser

                fa_dt = dateutil.parser.parse(forget_after)
                if fa_dt.tzinfo:
                    fa_dt = fa_dt.replace(tzinfo=None)
                if fa_dt < now_dt:
                    evicted_memories.append(mem["id"])
            except Exception as e:
                print(
                    f"[auto_forget] Failed to parse forgetAfter '{forget_after}': {e}"
                )

    sessions = kv.list(KV.sessions)
    for sess in sessions:
        sid = sess.get("id")
        if not sid:
            continue
        obs_list = kv.list(KV.observations(sid))
        for obs in obs_list:
            importance = obs.get("importance")
            ts = obs.get("timestamp")
            if importance is not None and ts:
                try:
                    import dateutil.parser

                    ts_dt = dateutil.parser.parse(ts)
                    if ts_dt.tzinfo:
                        ts_dt = ts_dt.replace(tzinfo=None)
                    age_days = (now_dt - ts_dt).days
                    if importance <= 2 and age_days > 180:
                        evicted_observations.append((sid, obs["id"]))
                except Exception as e:
                    print(f"[auto_forget] Failed to parse timestamp '{ts}': {e}")

    folder_pairs = kv.list(KV.folders)
    for entry in folder_pairs:
        fp = entry.get("folderPath")
        aid = entry.get("agentId")
        if not fp or not aid:
            continue
        obs_list = kv.list(KV.folder_obs(fp, aid))
        for obs in obs_list:
            obs_id = obs.get("id")
            if not obs_id:
                continue

            forget_after = obs.get("forgetAfter")
            is_expired = False
            if forget_after:
                try:
                    import dateutil.parser

                    fa_dt = dateutil.parser.parse(forget_after)
                    if fa_dt.tzinfo:
                        fa_dt = fa_dt.replace(tzinfo=None)
                    if fa_dt < now_dt:
                        is_expired = True
                except Exception as e:
                    print(
                        f"[auto_forget] Failed to parse folder obs forgetAfter '{forget_after}': {e}"
                    )

            is_stale_low_value = False
            importance = obs.get("importance")
            ts = obs.get("timestamp")
            if importance is not None and ts:
                try:
                    import dateutil.parser

                    ts_dt = dateutil.parser.parse(ts)
                    if ts_dt.tzinfo:
                        ts_dt = ts_dt.replace(tzinfo=None)
                    age_days = (now_dt - ts_dt).days
                    if importance <= 2 and age_days > 180:
                        is_stale_low_value = True
                except Exception as e:
                    print(
                        f"[auto_forget] Failed to parse folder obs timestamp '{ts}': {e}"
                    )

            if is_expired or is_stale_low_value:
                evicted_folder_observations.append((fp, aid, obs_id, obs))

    if not dry_run:
        for mem_id in evicted_memories:
            mem = kv.get(KV.memories, mem_id)
            kv.delete(KV.memories, mem_id)
            if mem and mem.get("imageRef"):
                ref = mem["imageRef"]
                refs = kv.get(KV.imageRefs, ref) or 0
                if refs > 0:
                    kv.set(KV.imageRefs, ref, refs - 1)
            if _legacy._search_service:
                _legacy._search_service.remove(mem_id)

        for sid, obs_id in evicted_observations:
            base_oid = obs_id.replace(":raw", "")
            obs = kv.get(KV.observations(sid), base_oid)
            raw_obs = kv.get(KV.observations(sid), f"{base_oid}:raw")

            kv.delete(KV.observations(sid), base_oid)
            kv.delete(KV.observations(sid), f"{base_oid}:raw")

            for o in (obs, raw_obs):
                if o:
                    img = o.get("imageData") or o.get("imageRef")
                    if img:
                        refs = kv.get(KV.imageRefs, img) or 0
                        if refs > 0:
                            kv.set(KV.imageRefs, img, refs - 1)

            if _legacy._search_service:
                _legacy._search_service.remove(base_oid)
                _legacy._search_service.remove(f"{base_oid}:raw")

        folder_deletes = {}
        for fp, aid, obs_id, obs in evicted_folder_observations:
            kv.delete(KV.folder_obs(fp, aid), obs_id)
            kv.delete(KV.obs_lookup, obs_id)

            if obs and isinstance(obs, dict) and obs.get("text"):
                fp_text = obs["text"][:4000]
                dedup_fp = hashlib.sha256(
                    fp_text.strip().lower().encode("utf-8")
                ).hexdigest()
                kv.delete(KV.obs_dedup(fp, aid), dedup_fp)

            if _legacy._search_service:
                _legacy._search_service.remove(obs_id)

            pair_key = (fp, aid)
            folder_deletes[pair_key] = folder_deletes.get(pair_key, 0) + 1

        for (fp, aid), count in folder_deletes.items():
            meta_scope = KV.folder_meta(fp, aid)
            meta = kv.get(meta_scope, "meta")
            if meta and isinstance(meta, dict):
                current_count = meta.get("obsCount", 0)
                meta["obsCount"] = max(0, current_count - count)
                kv.set(meta_scope, "meta", meta)

                index_key = f"{fp}:{aid}"
                index_entry = kv.get(KV.folders, index_key)
                if index_entry and isinstance(index_entry, dict):
                    index_entry["obsCount"] = meta["obsCount"]
                    kv.set(KV.folders, index_key, index_entry)

        if evicted_memories or evicted_observations or evicted_folder_observations:
            if _legacy._search_service:
                _legacy._search_service.schedule_persist()
            safe_audit(
                kv,
                "auto_forget",
                "mem::auto_forget",
                evicted_memories
                + [oid for _, oid in evicted_observations]
                + [oid for _, _, oid, _ in evicted_folder_observations],
                {
                    "evictedMemoriesCount": len(evicted_memories),
                    "evictedObservationsCount": len(evicted_observations)
                    + len(evicted_folder_observations),
                    "dryRun": False,
                },
            )
            commit_if_enabled(
                kv,
                f"Auto forget: evicted {len(evicted_memories)} memories, {len(evicted_observations) + len(evicted_folder_observations)} observations",
                "system",
            )

    return {
        "success": True,
        "evictedMemories": evicted_memories,
        "evictedObservations": [oid for _, oid in evicted_observations]
        + [oid for _, _, oid, _ in evicted_folder_observations],
        "evicted": len(evicted_memories)
        + len(evicted_observations)
        + len(evicted_folder_observations),
        "dryRun": dry_run,
    }


def health_check(kv: StateKV) -> Dict[str, Any]:
    from .. import legacy as _legacy

    db_status = "connected"
    if kv is None:
        db_status = "disconnected"
    else:
        try:
            kv._get_conn()
        except Exception:
            db_status = "disconnected"

    folder_count = 0
    agent_count = 0
    pair_count = 0
    observation_count = 0
    if kv is not None:
        try:
            folder_pairs = kv.list(KV.folders)
            pair_count = len(folder_pairs)
            unique_folders: Set[str] = set()
            unique_agents: Set[str] = set()
            for entry in folder_pairs:
                fp = entry.get("folderPath")
                aid = entry.get("agentId")
                if fp:
                    unique_folders.add(fp)
                if aid:
                    unique_agents.add(aid)
                observation_count += int(entry.get("obsCount") or 0)
            folder_count = len(unique_folders)
            agent_count = len(unique_agents)
        except Exception as e:
            print(f"[health_check] folder count failed: {e}")

    memory_count = 0
    if kv is not None:
        try:
            memory_count = len(kv.list(KV.memories))
        except Exception:
            pass

    bm25_index_size = 0
    try:
        bm25_index_size = (
            _legacy._search_service.bm25.size if _legacy._search_service else 0
        )
    except Exception:
        pass

    vector_index_size = 0
    try:
        if _legacy._search_service and _legacy._search_service.vector:
            vector_index_size = _legacy._search_service.vector.size
    except Exception:
        pass

    sync_status = "never"
    last_sync_at = None
    db_size_bytes = 0
    wal_size_bytes = 0
    try:
        sync_state_path = os.path.join(
            os.path.expanduser("~"), ".agentcache", ".sync_state"
        )
        if os.path.exists(sync_state_path):
            with open(sync_state_path, "r", encoding="utf-8") as _sf:
                _sync = json.loads(_sf.read())
            sync_status = _sync.get("sync_status", "never")
            last_sync_at = _sync.get("last_sync_at")
    except Exception:
        pass

    if kv is not None:
        try:
            db_stats = kv.stats()
            db_size_bytes = db_stats.get("db_size_bytes", 0)
            wal_size_bytes = db_stats.get("wal_size_bytes", 0)
        except Exception:
            pass

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "folderCount": folder_count,
        "agentCount": agent_count,
        "pairCount": pair_count,
        "observationCount": observation_count,
        "memoryCount": memory_count,
        "bm25IndexSize": bm25_index_size,
        "vectorIndexSize": vector_index_size,
        "dbPath": kv.db_path if kv else "",
        "dbSizeBytes": db_size_bytes,
        "walSizeBytes": wal_size_bytes,
        "syncStatus": sync_status,
        "lastSyncAt": last_sync_at,
    }


def rebuild_index(kv: StateKV) -> int:
    """Clear and rebuild the search index from all stored observations."""
    from .session_store import _get_observation_store

    store = _get_observation_store(kv)
    return store.rebuild_index()
