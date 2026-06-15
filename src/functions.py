import os
import re
import time
import uuid
import json
import hashlib
import datetime
import threading
from typing import Dict, Any, List, Optional, Tuple, Set
from db import StateKV
from search import (
    SearchIndex,
    VectorIndex,
    GeminiEmbeddingProvider,
    HybridSearch,
    base64_to_float32,
    float32_to_base64
)

# =====================================================================
# Global Variables / Module State
# =====================================================================
_bm25_index = SearchIndex()
_vector_index = VectorIndex()
_embedding_provider = None
_hybrid_search = HybridSearch(_bm25_index, _vector_index, None, None)
_index_persistence = None
_stream_broadcaster = None  # Callable: (payload) -> None

# KV scope registry — folder-based memory model
class KV:
    # ---- Folder memory scopes (new) ----

    # Global index of all (folder_path, agent_id) pairs known to the system.
    # Key = "{safe_folder_path}:{agent_id}", value = FolderIndexEntry dict.
    folders = "mem:folders"

    # Lookup index for O(1) observation hydration.
    # Scope = "mem:obs_lookup", Key = obs_id, Value = {"folderPath": folder_path, "agentId": agent_id}
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

    # Lessons — confidence-scored learning entries.
    lessons = "mem:lessons"

    # Legacy summary / profile / slot / image-ref scopes retained for legacy code paths.
    summaries = "mem:summaries"
    profiles = "mem:profiles"
    slots = "mem:slots"
    imageRefs = "mem:image-refs"

    # Global (cross-project) pinned slots.
    globalSlots = "mem:global-slots"

def get_current_project(kv: StateKV) -> Optional[str]:
    try:
        sessions = kv.list(KV.sessions)
        if not sessions:
            return None
        active_sessions = [s for s in sessions if s.get("status") == "active"]
        if active_sessions:
            active_sessions.sort(key=lambda s: s.get("updatedAt", ""), reverse=True)
            return active_sessions[0].get("project")
        sessions.sort(key=lambda s: s.get("updatedAt", ""), reverse=True)
        return sessions[0].get("project")
    except Exception:
        return None

def project_slots_scope(kv: StateKV, project: Optional[str] = None) -> str:
    if not project:
        project = get_current_project(kv)
    if not project:
        return KV.slots
    return f"mem:slots:{project}"

# =====================================================================
# Core Helpers & Utilities
# =====================================================================

def generate_id(prefix: str) -> str:
    t = int(time.time() * 1000)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    ts_str = ""
    while t > 0:
        ts_str = chars[t % 36] + ts_str
        t //= 36
    if not ts_str:
        ts_str = "0"
    rand = uuid.uuid4().hex[:12]
    return f"{prefix}_{ts_str}_{rand}"

def fingerprint_id(prefix: str, content: str) -> str:
    h = hashlib.sha256(content.strip().lower().encode('utf-8')).hexdigest()
    return f"{prefix}_{h[:16]}"

# ---- Folder-path normalisation (REQ-002, REQ-063, REQ-064, REQ-066) ----

_MAX_PATH_LEN = 512


def normalize_folder_path(path: str) -> str:
    """Normalize a folder path for safe use in KV scope keys.

    Steps applied in order:
    1. Cap the raw input at 512 characters (REQ-066).
    2. Apply ``os.path.normpath`` to collapse redundant separators and
       resolve any ``..`` components at the OS level.
    3. Convert all OS-native separators to forward slashes.
    4. Strip any remaining leading or trailing slashes.

    Raises:
        ValueError: if *path* is empty (before or after normalization), or
                    if the normalized result still contains a ``..`` segment,
                    which would indicate an attempt at path traversal
                    (REQ-064).

    Returns:
        A non-empty, forward-slash-separated string with no leading/trailing
        slashes and no ``..`` segments — safe for use as a KV scope fragment.

    Property (REQ-074): idempotent — applying this function twice yields
    the same result as applying it once.
    """
    if not path:
        raise ValueError("folder_path must not be empty")

    # 1. Length cap before any processing.
    path = path[:_MAX_PATH_LEN]

    # Pre-normalisation traversal check: reject any path that contains a ".."
    # component in the raw input before normpath has a chance to resolve it.
    # This catches inputs like "/home/user/../../etc/passwd" which normpath
    # would silently resolve to "etc/passwd" (REQ-064).
    raw_parts = path.replace("\\", "/").split("/")
    if any(part == ".." for part in raw_parts):
        raise ValueError(
            f"folder_path contains path traversal segment '..': {path!r}"
        )

    # 2. OS-level normalisation (resolves duplicate separators, etc.)
    normalized = os.path.normpath(path)

    # 3. Unify separators to forward slash.
    normalized = normalized.replace("\\", "/")

    # 4. Strip leading / trailing slashes.
    normalized = normalized.strip("/")

    # Guard: also reject any ".." that somehow survives normalisation.
    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(
            f"folder_path contains path traversal segment '..': {path!r}"
        )

    if not normalized:
        raise ValueError("folder_path is empty after normalization")

    return normalized


def validate_agent_id(agent_id: str) -> str:
    """Validate and sanitize an agent_id before use in KV scope keys.

    Strips surrounding whitespace and caps at 512 characters (REQ-066).

    Raises:
        ValueError: if *agent_id* is empty after stripping.

    Returns:
        Sanitized agent_id string.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")

    sanitized = agent_id.strip()[:_MAX_PATH_LEN]

    if not sanitized:
        raise ValueError("agent_id is empty after stripping whitespace")

    return sanitized


def auto_complete_old_active_sessions(kv: StateKV, current_session_id: str, project: Optional[str] = None, agent_id: Optional[str] = None) -> int:
    sessions = kv.list(KV.sessions)
    count = 0
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for s in sessions:
        if s.get("id") != current_session_id and s.get("status") == "active":
            if project and s.get("project") != project:
                continue
            if agent_id and s.get("agentId") != agent_id:
                continue
            s["status"] = "completed"
            if "endedAt" not in s:
                s["endedAt"] = now
            s["updatedAt"] = now
            kv.set(KV.sessions, s["id"], s)
            count += 1
    if count > 0:
        print(f"[session] Auto-completed {count} dangling active sessions.")
    return count

def jaccard_similarity(a: str, b: str) -> float:
    tokens_a = [t for t in a.split() if len(t) > 2]
    tokens_b = [t for t in b.split() if len(t) > 2]
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union

# =====================================================================
# Privacy & Data Scrubbing
# =====================================================================

PRIVATE_TAG_RE = re.compile(r'<private>[\s\S]*?</private>', re.IGNORECASE)

SECRET_PATTERN_SOURCES = [
    re.compile(r'(?:api[_-]?key|secret|token|password|credential|auth)[\s]*[=:]\s*["\']?[A-Za-z0-9_\-/.+]{20,}["\']?', re.IGNORECASE),
    re.compile(r'Bearer\s+[A-Za-z0-9._\-+/=]{20,}', re.IGNORECASE),
    re.compile(r'sk-proj-[A-Za-z0-9\-_]{20,}', re.IGNORECASE),
    re.compile(r'(?:sk|pk|rk|ak)-[A-Za-z0-9][A-Za-z0-9\-_]{19,}', re.IGNORECASE),
    re.compile(r'sk-ant-[A-Za-z0-9\-_]{20,}', re.IGNORECASE),
    re.compile(r'gh[pus]_[A-Za-z0-9]{36,}', re.IGNORECASE),
    re.compile(r'github_pat_[A-Za-z0-9_]{22,}', re.IGNORECASE),
    re.compile(r'xoxb-[A-Za-z0-9\-]+', re.IGNORECASE),
    re.compile(r'AKIA[0-9A-Z]{16}', re.IGNORECASE),
    re.compile(r'AIza[A-Za-z0-9\-_]{35}', re.IGNORECASE),
    re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', re.IGNORECASE),
    re.compile(r'npm_[A-Za-z0-9]{36}', re.IGNORECASE),
    re.compile(r'glpat-[A-Za-z0-9\-_]{20,}', re.IGNORECASE),
    re.compile(r'dop_v1_[A-Za-z0-9]{64}', re.IGNORECASE),
]

def strip_private_data(input_str: str) -> str:
    result = PRIVATE_TAG_RE.sub("[REDACTED]", input_str)
    for pattern in SECRET_PATTERN_SOURCES:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result

# =====================================================================
# Audit Log System
# =====================================================================

def record_audit(
    kv: StateKV,
    operation: str,
    function_id: str,
    target_ids: List[str],
    details: Dict[str, Any] = {},
    quality_score: Optional[float] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "id": generate_id("aud"),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "operation": operation,
        "userId": user_id,
        "functionId": function_id,
        "targetIds": target_ids,
        "details": details,
        "qualityScore": quality_score,
    }
    kv.set(KV.audit, entry["id"], entry)
    return entry

def safe_audit(
    kv: StateKV,
    operation: str,
    function_id: str,
    target_ids: List[str],
    details: Dict[str, Any] = {},
    quality_score: Optional[float] = None,
    user_id: Optional[str] = None,
) -> None:
    try:
        record_audit(kv, operation, function_id, target_ids, details, quality_score, user_id)
    except Exception as e:
        print(f"[audit] Failed to write audit: {e}")

def query_audit(
    kv: StateKV,
    filter_opts: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    all_entries = kv.list(KV.audit)
    entries = sorted(all_entries, key=lambda x: x.get("timestamp", ""), reverse=True)
    if not filter_opts:
        return entries[:100]

    op = filter_opts.get("operation")
    if op:
        entries = [e for e in entries if e.get("operation") == op]

    import dateutil.parser

    date_from = filter_opts.get("dateFrom")
    if date_from:
        try:
            dt_from = dateutil.parser.parse(date_from).replace(tzinfo=None)
            filtered_entries = []
            for e in entries:
                ts = e.get("timestamp")
                if ts:
                    try:
                        dt_ts = dateutil.parser.parse(ts).replace(tzinfo=None)
                        if dt_ts >= dt_from:
                            filtered_entries.append(e)
                    except Exception:
                        pass
            entries = filtered_entries
        except Exception:
            pass

    date_to = filter_opts.get("dateTo")
    if date_to:
        try:
            dt_to = dateutil.parser.parse(date_to).replace(tzinfo=None)
            filtered_entries = []
            for e in entries:
                ts = e.get("timestamp")
                if ts:
                    try:
                        dt_ts = dateutil.parser.parse(ts).replace(tzinfo=None)
                        if dt_ts <= dt_to:
                            filtered_entries.append(e)
                    except Exception:
                        pass
            entries = filtered_entries
        except Exception:
            pass

    limit = filter_opts.get("limit", 100)
    return entries[:limit]

# =====================================================================
# Image Store System
# =====================================================================

IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".agentmemory", "images")

def get_max_bytes() -> int:
    return int(os.getenv("AGENTMEMORY_IMAGE_STORE_MAX_BYTES", 500 * 1024 * 1024))

def is_managed_image_path(file_path: str) -> bool:
    if not file_path:
        return False
    resolved = os.path.abspath(file_path)
    normalized_images_dir = os.path.abspath(IMAGES_DIR)
    return resolved.startswith(normalized_images_dir + os.sep) or resolved == normalized_images_dir

def save_image_to_disk(base64_data: str) -> Tuple[str, int]:
    if not base64_data:
        return "", 0

    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR, exist_ok=True)

    clean_base64 = base64_data
    ext = "png"

    if base64_data.startswith("data:image/"):
        comma_idx = base64_data.find(",")
        if comma_idx != -1:
            meta = base64_data[:comma_idx]
            if "jpeg" in meta or "jpg" in meta:
                ext = "jpg"
            elif "webp" in meta:
                ext = "webp"
            elif "gif" in meta:
                ext = "gif"
            clean_base64 = base64_data[comma_idx + 1:]
    elif base64_data.startswith("/9j/"):
        ext = "jpg"

    h = hashlib.sha256(clean_base64.encode('utf-8')).hexdigest()
    file_path = os.path.join(IMAGES_DIR, f"{h}.{ext}")

    if os.path.exists(file_path):
        return file_path, 0

    import base64
    buffer = base64.b64decode(clean_base64)
    with open(file_path, "wb") as f:
        f.write(buffer)

    size = os.path.getsize(file_path)
    return file_path, size

def delete_image(file_path: Optional[str]) -> int:
    if not file_path or not is_managed_image_path(file_path):
        return 0
    try:
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            os.remove(file_path)
            return size
    except Exception as e:
        print(f"[agentmemory] Failed to delete image context: {e}")
    return 0

def touch_image(file_path: str) -> None:
    if not file_path or not is_managed_image_path(file_path):
        return
    try:
        if os.path.exists(file_path):
            os.utime(file_path, None)
    except Exception:
        pass

# =====================================================================
# Index Persistence System (JSON Sharded)
# =====================================================================

class IndexPersistence:
    """Persist BM25 and vector indexes to the KV store with a debounce queue.

    A4.1: schedule_save() uses a threading.Timer that resets on each call and
    fires the actual save() after DEBOUNCE_SECONDS of inactivity.  This prevents
    a persistence write on every single observation under high throughput.

    A4.2: save() skips writing an index that has not been dirtied since the last
    save (relies on SearchIndex._dirty / VectorIndex._dirty flags).
    """

    DEBOUNCE_SECONDS: float = 5.0

    def __init__(self, kv: StateKV, bm25: SearchIndex, vector: Optional[VectorIndex]):
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
            # A4.2: skip save if neither index is dirty
            bm25_dirty = getattr(self.bm25, "_dirty", True)
            vector_dirty = self.vector and getattr(self.vector, "_dirty", True)

            if bm25_dirty:
                self.save_sharded_index(
                    json.dumps(self.bm25.serialize_data()),
                    "data:manifest",
                    "data",
                    "mem:index:bm25:bm25:"
                )
                self.bm25._dirty = False  # A4.2 — reset after save

            if self.vector and vector_dirty:
                self.save_sharded_index(
                    json.dumps(self.vector.serialize_data()),
                    "vectors:manifest",
                    "vectors",
                    "mem:index:bm25:vectors:"
                )
                self.vector._dirty = False  # A4.2 — reset after save

            if not bm25_dirty and not vector_dirty:
                print("[index persistence] indexes not dirty — skipping save")
        except Exception as e:
            print(f"[index persistence] failed to save index: {e}")

    def save_sharded_index(self, serialized: str, manifest_key: str, legacy_key: str, scope_prefix: str) -> None:
        previous = self.kv.get(KV.bm25Index, manifest_key)
        generation = generate_id("idx")
        chunk_chars = 2000000
        shards = []
        chunks = []

        offset = 0
        shard_idx = 0
        while offset < len(serialized):
            scope = f"{scope_prefix}{generation}:{str(shard_idx).zfill(5)}"
            chunk = serialized[offset:offset + chunk_chars]
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
            "chars": len(serialized)
        }

        self.kv.set(KV.bm25Index, manifest_key, next_manifest)
        self.kv.delete(KV.bm25Index, legacy_key)

        # Cleanup ALL obsolete shards starting with scope_prefix that are NOT in the current shards
        try:
            conn = self.kv._get_conn()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT DISTINCT scope FROM kv_store WHERE scope LIKE ?",
                    (scope_prefix + "%",)
                )
                rows = cursor.fetchall()
                current_scopes = {s["scope"] for s in shards}
                to_delete = []
                for row in rows:
                    scope_name = row["scope"]
                    if scope_name not in current_scopes:
                        to_delete.append(scope_name)
                
                if to_delete:
                    for i in range(0, len(to_delete), 50):
                        chunk_delete = to_delete[i:i + 50]
                        format_strings = ','.join(['?'] * len(chunk_delete))
                        cursor.execute(
                            f"DELETE FROM kv_store WHERE scope IN ({format_strings})",
                            tuple(chunk_delete)
                        )
                        conn.commit()
            finally:
                cursor.close()
        except Exception as ex:
            print(f"[index persistence] error cleaning up obsolete shards: {ex}")

        if previous and isinstance(previous, dict) and previous.get("v") == 1 and isinstance(previous.get("shards"), list):
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
                    return None
                chunks.append(chunk)
            return "".join(chunks)

        legacy = self.kv.get(KV.bm25Index, legacy_key)
        if isinstance(legacy, str):
            return legacy
        return None

# =====================================================================
# Vector Index / Embedding Helpers
# =====================================================================

def clip_embed_input(text: str) -> str:
    EMBED_MAX_CHARS = 16000
    if len(text) <= EMBED_MAX_CHARS:
        return text
    return text[:EMBED_MAX_CHARS]

def get_agent_id() -> Optional[str]:
    return os.getenv("AGENT_ID") or None

def commit_if_enabled(kv: StateKV, message: str, agent_id: Optional[str]) -> Optional[str]:
    return kv.commit_version(message, agent_id or "unknown-agent")


def is_agent_scope_isolated() -> bool:
    return os.getenv("AGENTMEMORY_AGENT_SCOPE") == "isolated"

def is_auto_compress_enabled() -> bool:
    return os.getenv("AGENTMEMORY_AUTO_COMPRESS") == "true"

def is_slots_enabled() -> bool:
    return os.getenv("AGENTMEMORY_SLOTS") == "true"

def is_reflect_enabled() -> bool:
    return os.getenv("AGENTMEMORY_REFLECT") == "true"

def is_graph_extraction_enabled() -> bool:
    return os.getenv("GRAPH_EXTRACTION_ENABLED") == "true"

def is_consolidation_enabled() -> bool:
    val = os.getenv("CONSOLIDATION_ENABLED")
    if val in ("false", "0"):
        return False
    if val in ("true", "1"):
        return True
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

def vector_index_add_guarded(
    obs_id: str,
    session_id: str,
    text: str,
    context: Dict[str, Any]
) -> bool:
    vi = _vector_index
    ep = _embedding_provider
    if not vi or not ep:
        return False
    try:
        clipped = clip_embed_input(text)
        embedding = ep.embed(clipped)
        if len(embedding) != ep.dimensions:
            print(f"[vector-index] Dimension mismatch: expected {ep.dimensions}, got {len(embedding)}")
            return False
        vi.add(obs_id, session_id, embedding)
        return True
    except Exception as e:
        print(f"[vector-index] Embed failed: {e}")
        return False

# =====================================================================
# Observation System (Observe, Synthetic Compression)
# =====================================================================

def extract_image(d: Any) -> Optional[str]:
    if not d:
        return None
    if isinstance(d, str):
        if d.startswith("data:image/") or d.startswith("iVBORw0KGgo") or d.startswith("/9j/"):
            return d
        return None
    if isinstance(d, dict):
        for k in ["image_data", "image_path", "imageBase64", "imagePath"]:
            if isinstance(d.get(k), str):
                return d[k]
        for key, val in d.items():
            match = extract_image(val)
            if match:
                return match
    return None

def infer_type(tool_name: Optional[str], hook_type: str) -> str:
    if hook_type == "post_tool_failure":
        return "error"
    if hook_type == "prompt_submit":
        return "conversation"
    if hook_type in ("subagent_stop", "task_completed"):
        return "subagent"
    if hook_type == "notification":
        return "notification"

    if not tool_name:
        return "other"

    n = re.sub(r'([a-z])([A-Z])', r'\1_\2', tool_name)
    n = re.sub(r'[-\s]+', '_', n).lower()

    def has_word(word: str) -> bool:
        return bool(re.search(rf"(^|_){word}(_|$)", n)) or n == word or n.endswith(word) or n.startswith(word)

    if any(has_word(w) for w in ["fetch", "http", "web"]):
        return "web_fetch"
    if any(has_word(w) for w in ["grep", "search", "glob", "find"]):
        return "search"
    if any(has_word(w) for w in ["bash", "shell", "exec", "run"]):
        return "command_run"
    if any(has_word(w) for w in ["edit", "update", "patch", "replace"]):
        return "file_edit"
    if any(has_word(w) for w in ["write", "create"]):
        return "file_write"
    if any(has_word(w) for w in ["read", "view"]):
        return "file_read"
    if any(has_word(w) for w in ["task", "agent"]):
        return "subagent"
    return "other"

def extract_files(input_data: Any) -> List[str]:
    if not input_data or not isinstance(input_data, dict):
        return []
    out = set()
    for key in ["file_path", "filepath", "path", "filePath", "file", "pattern"]:
        v = input_data.get(key)
        if isinstance(v, str) and 0 < len(v) < 512:
            out.add(v)
    return list(out)

def stringify_for_narrative(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v)
    except Exception:
        return str(v)

def build_synthetic_compression(raw: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = raw.get("toolName") or raw.get("hookType")
    input_str = stringify_for_narrative(raw.get("toolInput"))
    output_str = stringify_for_narrative(raw.get("toolOutput"))
    prompt_str = raw.get("userPrompt") or ""

    parts = [s for s in [prompt_str, input_str, output_str] if len(s) > 0]
    narrative = " | ".join(parts)
    if len(narrative) > 400:
        narrative = narrative[:399] + "\u2026"

    title = tool_name or "observation"
    if len(title) > 80:
        title = title[:79] + "\u2026"

    subtitle = None
    if input_str:
        subtitle = input_str
        if len(subtitle) > 120:
            subtitle = subtitle[:119] + "\u2026"

    res = {
        "id": raw["id"],
        "sessionId": raw["sessionId"],
        "timestamp": raw["timestamp"],
        "type": infer_type(raw.get("toolName"), raw["hookType"]),
        "title": title,
        "subtitle": subtitle,
        "facts": [],
        "narrative": narrative,
        "concepts": [],
        "files": extract_files(raw.get("toolInput")),
        "importance": 5,
        "confidence": 0.3,
    }
    for k in ["modality", "imageData", "agentId"]:
        if raw.get(k) is not None:
            res[k] = raw[k]
    return res

def observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    session_id = payload.get("sessionId")
    hook_type = payload.get("hookType")
    timestamp = payload.get("timestamp")

    if not session_id or not hook_type or not timestamp:
        raise ValueError("Invalid payload: sessionId, hookType, and timestamp are required")

    obs_id = generate_id("obs")
    sanitized_data = payload.get("data")
    try:
        json_str = json.dumps(payload.get("data"))
        sanitized = strip_private_data(json_str)
        sanitized_data = json.loads(sanitized)
    except Exception:
        sanitized_data = strip_private_data(str(payload.get("data")))

    raw = {
        "id": obs_id,
        "sessionId": session_id,
        "timestamp": timestamp,
        "hookType": hook_type,
        "raw": sanitized_data,
    }

    extracted_img = extract_image(sanitized_data)
    if isinstance(sanitized_data, dict):
        if hook_type in ("post_tool_use", "post_tool_failure"):
            raw["toolName"] = sanitized_data.get("tool_name")
            raw["toolInput"] = sanitized_data.get("tool_input")
            raw["toolOutput"] = sanitized_data.get("tool_output") or sanitized_data.get("error")
        if hook_type == "prompt_submit":
            raw["userPrompt"] = sanitized_data.get("prompt")
        if extracted_img:
            raw["modality"] = "mixed" if (raw.get("toolInput") or raw.get("toolOutput") or raw.get("userPrompt")) else "image"
    elif isinstance(sanitized_data, str) and extracted_img:
        raw["modality"] = "image"

    max_obs = int(os.getenv("MAX_OBS_PER_SESSION", "500"))
    if max_obs > 0:
        existing = kv.list(KV.observations(session_id))
        actual_obs_count = sum(1 for o in existing if not str(o.get("id", "")).endswith(":raw"))
        if actual_obs_count >= max_obs:
            raise ValueError(f"Session observation limit reached ({max_obs})")

    existing_session = kv.get(KV.sessions, session_id)
    inherited_agent_id = existing_session.get("agentId") if existing_session else get_agent_id()
    if inherited_agent_id:
        raw["agentId"] = inherited_agent_id

    if extracted_img and (extracted_img.startswith("data:image/") or extracted_img.startswith("iVBORw0KGgo") or extracted_img.startswith("/9j/")):
        try:
            file_path, bytes_written = save_image_to_disk(extracted_img)
            raw["imageData"] = file_path
            
            # Increment image ref count
            img_refs = kv.get(KV.imageRefs, file_path) or 0
            kv.set(KV.imageRefs, file_path, img_refs + 1)
        except Exception as ex:
            print(f"[image store] failed: {ex}")

    # Set raw observation
    raw["id"] = f"{obs_id}:raw"
    kv.set(KV.observations(session_id), raw["id"], raw)

    # Stream raw observation
    broadcast_stream({
        "type": "raw_observation",
        "sessionId": session_id,
        "data": {
            "type": "raw",
            "observation": raw,
            "sessionId": session_id
        }
    })

    if existing_session:
        updates = [
            {"type": "set", "path": "updatedAt", "value": datetime.datetime.utcnow().isoformat() + "Z"},
            {"type": "set", "path": "observationCount", "value": (existing_session.get("observationCount") or 0) + 1}
        ]
        if not existing_session.get("firstPrompt") and isinstance(raw.get("userPrompt"), str):
            trimmed = " ".join(raw["userPrompt"].split()).strip()
            if trimmed:
                updates.append({"type": "set", "path": "firstPrompt", "value": trimmed[:200]})
        kv.update(KV.sessions, session_id, updates)
    else:
        project = payload.get("project") or "unknown"
        auto_complete_old_active_sessions(kv, session_id, project=project, agent_id=inherited_agent_id)
        cwd = payload.get("cwd") or os.getcwd()
        trimmed_prompt = None
        if isinstance(raw.get("userPrompt"), str):
            trimmed_prompt = " ".join(raw["userPrompt"].split()).strip()[:200]
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        new_sess = {
            "id": session_id,
            "project": project,
            "cwd": cwd,
            "startedAt": payload.get("timestamp") or ts,
            "updatedAt": ts,
            "status": "active",
            "observationCount": 1,
        }
        if inherited_agent_id:
            new_sess["agentId"] = inherited_agent_id
        if trimmed_prompt:
            new_sess["firstPrompt"] = trimmed_prompt
        kv.set(KV.sessions, session_id, new_sess)

    # Perform synthetic compression (we default to synthetic)
    raw_for_synthetic = dict(raw)
    raw_for_synthetic["id"] = obs_id
    synthetic = build_synthetic_compression(raw_for_synthetic)
    for k in ["hookType", "raw", "toolName", "toolInput", "toolOutput", "userPrompt"]:
        if k in raw_for_synthetic:
            synthetic[k] = raw_for_synthetic[k]
    kv.set(KV.observations(session_id), obs_id, synthetic)
    _bm25_index.add(synthetic)

    comb_text = synthetic["title"] + " " + (synthetic.get("narrative") or "")
    vector_index_add_guarded(synthetic["id"], synthetic["sessionId"], comb_text, {"kind": "synthetic", "logId": synthetic["id"]})

    if _index_persistence:
        _index_persistence.schedule_save()

    # Stream compressed observation
    broadcast_stream({
        "type": "compressed_observation",
        "sessionId": session_id,
        "data": {
            "type": "compressed",
            "observation": synthetic,
            "sessionId": session_id
        }
    })

    # Commit to Dolt
    commit_if_enabled(kv, f"Observe: {synthetic.get('title', 'observation')} in session {session_id[:8]}", synthetic.get("agentId"))

    return {"observationId": obs_id}


# =====================================================================
# Folder-Based Observation Ingestion (folder_observe)
# =====================================================================

def folder_observe(kv: StateKV, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a new observation scoped to a (folder_path, agent_id) pair.

    Required payload fields:
        folderPath: str      — absolute path of working directory
        agentId: str         — identity of the agent making the observation
        text: str            — human-readable observation content
        timestamp: str       — ISO 8601 UTC

    Optional payload fields:
        type: str            — observation type (default inferred or "other")
        title: str           — short title (auto-generated from text[:80] if absent)
        concepts: list[str]  — concept tags (default [])
        files: list[str]     — referenced file paths (default [] or extracted)
        importance: int      — 1-10 (default 5, clamped)

    Returns: {"observationId": str}
    Raises:  ValueError if required fields are missing or folder cap exceeded.
    """
    # 1. Validate required fields (REQ-008)
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

    # 2. Normalize folder_path and validate agent_id (REQ-002, REQ-063, REQ-064, REQ-066)
    folder_path = normalize_folder_path(folder_path_raw)
    agent_id = validate_agent_id(agent_id_raw)

    # 3. Strip private data and cap text (REQ-009, REQ-007)
    safe_text = strip_private_data(text_raw)
    safe_text = safe_text[:4000]

    # 10. Enforce MAX_OBS_PER_FOLDER cap before writing (REQ-015)
    max_obs = int(os.getenv("MAX_OBS_PER_FOLDER", "2000"))
    if max_obs > 0:
        existing_obs = kv.list(KV.folder_obs(folder_path, agent_id))
        if len(existing_obs) >= max_obs:
            raise ValueError(f"Folder observation limit reached ({max_obs})")

    # 4. Generate obs_id (REQ-010)
    obs_id = generate_id("fobs")

    # 5. Determine optional fields
    obs_type = payload.get("type")
    if not obs_type:
        # Use infer_type with no tool_name; pass "other" as hook_type
        obs_type = infer_type(None, "other")

    title = payload.get("title")
    if not title:
        title = safe_text[:80]

    concepts = payload.get("concepts") or []
    if not isinstance(concepts, list):
        concepts = []

    files = payload.get("files")
    if not isinstance(files, list):
        # Try to extract file refs from the payload itself
        files = extract_files(payload)

    raw_importance = payload.get("importance")
    if raw_importance is None:
        importance = 5
    else:
        try:
            importance = max(1, min(10, int(raw_importance)))
        except (TypeError, ValueError):
            importance = 5

    # 5. Build FolderObservation dict (REQ-003)
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

    # 6. Write observation to KV (REQ-001)
    obs_scope = KV.folder_obs(folder_path, agent_id)
    kv.set(obs_scope, obs_id, obs)

    # Write coordinate lookup mapping
    kv.set(KV.obs_lookup, obs_id, {
        "folderPath": folder_path,
        "agentId": agent_id,
    })

    # 7. Upsert folder metadata (REQ-005)
    meta_scope = KV.folder_meta(folder_path, agent_id)
    meta = kv.get(meta_scope, "meta") or {
        "folderPath": folder_path,
        "agentId": agent_id,
        "obsCount": 0,
        "lastUpdated": timestamp,
        "summary": None,
    }
    meta["obsCount"] = meta.get("obsCount", 0) + 1
    meta["lastUpdated"] = timestamp
    kv.set(meta_scope, "meta", meta)

    # 8. Upsert global folders index entry (REQ-004, REQ-011)
    index_key = f"{folder_path}:{agent_id}"
    kv.set(KV.folders, index_key, {
        "folderPath": folder_path,
        "agentId": agent_id,
        "lastUpdated": meta["lastUpdated"],
        "obsCount": meta["obsCount"],
    })

    # 9. Add to BM25 index and vector index (REQ-012)
    try:
        _bm25_index.add(obs)
    except Exception as ex:
        print(f"[bm25] folder_observe add failed: {ex}")

    comb_text = title + " " + safe_text
    vector_index_add_guarded(obs_id, folder_path, comb_text, {"kind": "folder_obs", "logId": obs_id})

    if _index_persistence:
        _index_persistence.schedule_save()

    # 11. Write audit log entry (REQ-014)
    kv.commit_version(f"folder_observe: {obs_id}", agent_id)

    # 12. Broadcast via WebSocket stream (REQ-013)
    broadcast_stream({
        "type": "folder_observation",
        "folderPath": folder_path,
        "agentId": agent_id,
        "data": obs,
    })

    return {"observationId": obs_id}


# =====================================================================
# Folder-Based Search (folder_search)
# =====================================================================

def folder_search(
    kv: StateKV,
    query: str,
    limit: int = 20,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search across all folder observations (and global memories) using BM25 + vector hybrid search.

    Steps:
    1. Run hybrid search to obtain up to ``limit * 2`` candidate obs_ids with scores.
    2. Hydrate each candidate by looking up the observation in the KV store:
       - Iterate ``KV.folders`` index to discover all (folder_path, agent_id) pairs.
       - For each pair, load observations from ``KV.folder_obs`` and build an obs_id → obs map.
    3. Apply ``folder_path`` and ``agent_id`` post-filters to folder observations.
    4. Also include matching global memories from ``KV.memories``.
    5. Return results sorted by score descending, capped at ``limit``.

    Each result dict contains at minimum: ``folderPath``, ``agentId``, ``score``,
    plus all fields from the underlying FolderObservation or Memory object.

    Requirements: REQ-016, REQ-017, REQ-018, REQ-019
    """
    if not query or not query.strip():
        return []

    candidates = _hybrid_search.search(query, limit * 2)

    # --- Hydrate candidates from the search results (REQ-018, REQ-019) ---
    results: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for candidate in candidates:
        obs_id = candidate.get("obsId") or candidate.get("id", "")
        score = candidate.get("combinedScore") or candidate.get("score", 0.0)

        if not obs_id or obs_id in seen_ids:
            continue

        # 1. Try folder observation first via O(1) coordinate lookup index
        lookup = kv.get(KV.obs_lookup, obs_id)
        if lookup and isinstance(lookup, dict):
            fp = lookup.get("folderPath")
            aid = lookup.get("agentId")
            if fp and aid:
                if folder_path is not None and fp != folder_path:
                    continue
                if agent_id is not None and aid != agent_id:
                    continue

                obs = kv.get(KV.folder_obs(fp, aid), obs_id)
                if obs and isinstance(obs, dict):
                    result = dict(obs)
                    result["score"] = score
                    result.setdefault("folderPath", fp)
                    result.setdefault("agentId", aid)
                    results.append(result)
                    seen_ids.add(obs_id)
                    continue

        # 2. Fallback scan for unindexed folder observations (e.g. from prior versions)
        if obs_id.startswith("fobs_"):
            found = False
            for entry in kv.list(KV.folders):
                fp = entry.get("folderPath", "")
                aid = entry.get("agentId", "")
                if not fp or not aid:
                    continue
                if folder_path is not None and fp != folder_path:
                    continue
                if agent_id is not None and aid != agent_id:
                    continue
                obs = kv.get(KV.folder_obs(fp, aid), obs_id)
                if obs and isinstance(obs, dict):
                    result = dict(obs)
                    result["score"] = score
                    result.setdefault("folderPath", fp)
                    result.setdefault("agentId", aid)
                    results.append(result)
                    seen_ids.add(obs_id)
                    # Lazy backfill the lookup index
                    kv.set(KV.obs_lookup, obs_id, {"folderPath": fp, "agentId": aid})
                    found = True
                    break
            if found:
                continue

        # 3. Try global memory (REQ-018)
        mem = kv.get(KV.memories, obs_id)
        if mem and isinstance(mem, dict):
            if mem.get("isLatest") is not False:
                result = dict(mem)
                result["score"] = score
                result.setdefault("folderPath", "")
                result.setdefault("agentId", mem.get("agentId") or "")
                results.append(result)
                seen_ids.add(obs_id)
                continue

        # obs_id not found in either map — skip (stale index entry)

    # Sort by score descending and cap at limit (REQ-016)
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return results[:limit]


def folder_timeline(
    kv: StateKV,
    limit: int = 100,
    folder_path: Optional[str] = None,
    agent_id: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a folder activity feed — observations sorted by timestamp descending.

    Algorithm:
    1. List all (folder, agent) pairs from ``KV.folders``.
    2. Apply ``folder_path`` exact-match filter if provided.
    3. Apply ``agent_id`` exact-match filter if provided.
    4. For each remaining pair, load all observations from
       ``KV.folder_obs(entry["folderPath"], entry["agentId"])``.
    5. Apply ``before`` ISO timestamp upper-bound filter:
       exclude obs where ``obs["timestamp"] >= before``.
    6. Apply ``after`` ISO timestamp lower-bound filter:
       exclude obs where ``obs["timestamp"] <= after``.
    7. Sort all collected observations by ``timestamp`` descending.
    8. Return the first ``limit`` entries.

    Postconditions (REQ-071):
    - ``len(result) <= limit``
    - All results satisfy the provided filter conditions.
    - Results are in non-increasing timestamp order.

    Requirements: REQ-020, REQ-021, REQ-022
    """
    # Step 1 — load the global folders index
    index_entries = kv.list(KV.folders)

    # Step 2 — filter by folder_path exact match (REQ-021)
    if folder_path is not None:
        index_entries = [e for e in index_entries if e.get("folderPath") == folder_path]

    # Step 3 — filter by agent_id exact match (REQ-021)
    if agent_id is not None:
        index_entries = [e for e in index_entries if e.get("agentId") == agent_id]

    all_obs: List[Dict[str, Any]] = []

    for entry in index_entries:
        fp = entry.get("folderPath", "")
        aid = entry.get("agentId", "")
        if not fp or not aid:
            continue

        # Step 4 — load observations for this pair
        obs_scope = KV.folder_obs(fp, aid)
        obs_list = kv.list(obs_scope)

        # Step 5 — apply before filter: exclude obs where timestamp >= before (REQ-021)
        if before is not None:
            obs_list = [o for o in obs_list if o.get("timestamp", "") < before]

        # Step 6 — apply after filter: exclude obs where timestamp <= after (REQ-021)
        if after is not None:
            obs_list = [o for o in obs_list if o.get("timestamp", "") > after]

        all_obs.extend(obs_list)

    # Step 7 — sort by timestamp descending (REQ-071)
    all_obs.sort(key=lambda o: o.get("timestamp", ""), reverse=True)

    # Step 8 — return at most limit entries (REQ-022)
    return all_obs[:limit]

# =====================================================================
# Memory System (Remember, Forget, Evolve)
# =====================================================================

def memory_to_observation(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": memory["id"],
        "sessionId": memory.get("sessionIds", ["memory"])[0] if memory.get("sessionIds") else "memory",
        "timestamp": memory["createdAt"],
        "type": "decision",
        "title": memory["title"],
        "facts": [memory["content"]],
        "narrative": memory["content"],
        "concepts": memory.get("concepts", []),
        "files": memory.get("files", []),
        "importance": memory.get("strength", 7),
    }

def remember(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    content = data.get("content")
    if not content or not content.strip():
        raise ValueError("content is required")
    content = strip_private_data(content)

    concepts = data.get("concepts") or []
    files = data.get("files") or []
    source_obs = data.get("sourceObservationIds") or []
    ttl_days = data.get("ttlDays")
    mem_type = data.get("type") or "fact"
    project = data.get("project")
    if project:
        project = project.strip()

    now = datetime.datetime.utcnow().isoformat() + "Z"
    existing_memories = kv.list(KV.memories)
    superseded_id = None
    superseded_version = 1
    superseded_memory = None
    lower_content = content.lower()

    for existing in existing_memories:
        if existing.get("isLatest") is False:
            continue
        if project and existing.get("project") and existing["project"] != project:
            continue
        similarity = jaccard_similarity(lower_content, existing.get("content", "").lower())
        if similarity > 0.7:
            superseded_id = existing["id"]
            superseded_version = existing.get("version") or 1
            superseded_memory = existing
            break

    call_agent_id = data.get("agentId") or get_agent_id()
    new_mem = {
        "id": generate_id("mem"),
        "createdAt": now,
        "updatedAt": now,
        "type": mem_type,
        "title": content[:80],
        "content": content,
        "concepts": concepts,
        "files": files,
        "sessionIds": [],
        "strength": 7,
        "version": superseded_version + 1 if superseded_id else 1,
        "parentId": superseded_id,
        "supersedes": [superseded_id] if superseded_id else [],
        "sourceObservationIds": [i for i in source_obs if i],
        "isLatest": True,
    }
    if call_agent_id:
        new_mem["agentId"] = call_agent_id
    if project:
        new_mem["project"] = project

    if ttl_days and isinstance(ttl_days, (int, float)) and ttl_days > 0:
        forget_time = datetime.datetime.utcnow() + datetime.timedelta(days=ttl_days)
        new_mem["forgetAfter"] = forget_time.isoformat() + "Z"

    if superseded_memory:
        superseded_memory["isLatest"] = False
        kv.set(KV.memories, superseded_memory["id"], superseded_memory)

    kv.set(KV.memories, new_mem["id"], new_mem)

    try:
        _bm25_index.add(memory_to_observation(new_mem))
    except Exception as ex:
        print(f"[bm25] memory add failed: {ex}")

    comb_text = new_mem["title"] + " " + new_mem["content"]
    vector_index_add_guarded(new_mem["id"], "memory", comb_text, {"kind": "memory", "logId": new_mem["id"]})

    if _index_persistence:
        _index_persistence.schedule_save()

    # Commit to Dolt
    commit_if_enabled(kv, f"Remember: {new_mem.get('title', '')}", new_mem.get("agentId"))

    return {"success": True, "memory": new_mem}


def forget(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a global memory, a folder (folder_path+agent_id), or specific observations.

    Dispatch rules (REQ-029, REQ-030, REQ-031, REQ-032, REQ-033):
      1. ``memoryId`` present              → delete that global memory from KV.memories
      2. ``folderPath + agentId`` present,
         no ``observationIds``             → delete ALL observations for that pair,
                                             remove BM25 entries, delete folder_meta,
                                             remove from KV.folders index
      3. ``folderPath + agentId +
         observationIds`` present          → delete only the listed observations,
                                             decrement obsCount in metadata

    Legacy session-based paths are preserved for backward compatibility.
    """
    memory_id = data.get("memoryId")
    session_id = data.get("sessionId")
    folder_path_raw = data.get("folderPath")
    agent_id_raw = data.get("agentId")
    obs_ids = data.get("observationIds") or []
    deleted = 0
    deleted_mem_ids = []
    deleted_obs_ids = []
    deleted_session = False

    # ------------------------------------------------------------------
    # Path 1: delete a global memory (REQ-029)
    # ------------------------------------------------------------------
    if memory_id:
        mem = kv.get(KV.memories, memory_id)
        kv.delete(KV.memories, memory_id)
        if mem and mem.get("imageRef"):
            ref = mem["imageRef"]
            refs = kv.get(KV.imageRefs, ref) or 0
            if refs > 0:
                kv.set(KV.imageRefs, ref, refs - 1)
        _bm25_index.remove(memory_id)
        if _vector_index:
            _vector_index.remove(memory_id)
        deleted_mem_ids.append(memory_id)
        deleted += 1

    # ------------------------------------------------------------------
    # Path 2 & 3: folder-based deletion (REQ-030, REQ-031, REQ-032, REQ-033)
    # ------------------------------------------------------------------
    if folder_path_raw and agent_id_raw:
        try:
            fp = normalize_folder_path(folder_path_raw)
            aid = validate_agent_id(agent_id_raw)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "deleted": 0}

        obs_scope = KV.folder_obs(fp, aid)
        meta_scope = KV.folder_meta(fp, aid)
        index_key = f"{fp}:{aid}"

        if obs_ids:
            # ----------------------------------------------------------
            # Path 3: partial deletion — only the listed obs IDs (REQ-031)
            # ----------------------------------------------------------
            partial_deleted = 0
            for oid in obs_ids:
                existed = kv.delete(obs_scope, oid)
                if existed:
                    kv.delete(KV.obs_lookup, oid)
                    _bm25_index.remove(oid)
                    if _vector_index:
                        _vector_index.remove(oid)
                    deleted_obs_ids.append(oid)
                    partial_deleted += 1
                    deleted += 1

            # Decrement obsCount in metadata
            if partial_deleted > 0:
                meta = kv.get(meta_scope, "meta")
                if meta and isinstance(meta, dict):
                    current_count = meta.get("obsCount", 0)
                    meta["obsCount"] = max(0, current_count - partial_deleted)
                    kv.set(meta_scope, "meta", meta)
                    # Also sync the folders index entry
                    index_entry = kv.get(KV.folders, index_key)
                    if index_entry and isinstance(index_entry, dict):
                        index_entry["obsCount"] = meta["obsCount"]
                        kv.set(KV.folders, index_key, index_entry)
        else:
            # ----------------------------------------------------------
            # Path 2: full pair deletion (REQ-030, REQ-032)
            # ----------------------------------------------------------
            all_obs = kv.list(obs_scope)
            for obs in all_obs:
                obs_id = obs.get("id")
                if obs_id:
                    kv.delete(obs_scope, obs_id)
                    kv.delete(KV.obs_lookup, obs_id)
                    _bm25_index.remove(obs_id)
                    if _vector_index:
                        _vector_index.remove(obs_id)
                    deleted_obs_ids.append(obs_id)
                    deleted += 1

            # Delete folder metadata entry
            kv.delete(meta_scope, "meta")

            # Remove from global folders index
            kv.delete(KV.folders, index_key)

    # ------------------------------------------------------------------
    # Legacy: session-based deletion (unchanged)
    # ------------------------------------------------------------------
    if session_id and obs_ids:
        for oid in obs_ids:
            base_oid = oid.replace(":raw", "")
            obs = kv.get(KV.observations(session_id), base_oid)
            raw_obs = kv.get(KV.observations(session_id), f"{base_oid}:raw")

            kv.delete(KV.observations(session_id), base_oid)
            kv.delete(KV.observations(session_id), f"{base_oid}:raw")
            kv.delete(KV.obs_lookup, base_oid)

            for o in (obs, raw_obs):
                if o:
                    img = o.get("imageData") or o.get("imageRef")
                    if img:
                        refs = kv.get(KV.imageRefs, img) or 0
                        if refs > 0:
                            kv.set(KV.imageRefs, img, refs - 1)

            _bm25_index.remove(base_oid)
            _bm25_index.remove(f"{base_oid}:raw")
            if _vector_index:
                _vector_index.remove(base_oid)
                _vector_index.remove(f"{base_oid}:raw")
            deleted_obs_ids.append(oid)
            deleted += 1

    if session_id and not obs_ids and not memory_id and not folder_path_raw:
        obs_list = kv.list(KV.observations(session_id))
        for obs in obs_list:
            kv.delete(KV.observations(session_id), obs["id"])
            kv.delete(KV.obs_lookup, obs["id"])
            img = obs.get("imageData") or obs.get("imageRef")
            if img:
                refs = kv.get(KV.imageRefs, img) or 0
                if refs > 0:
                    kv.set(KV.imageRefs, img, refs - 1)
            _bm25_index.remove(obs["id"])
            if _vector_index:
                _vector_index.remove(obs["id"])
            deleted_obs_ids.append(obs["id"])
            deleted += 1
        kv.delete(KV.sessions, session_id)
        kv.delete(KV.summaries, session_id)
        deleted_session = True
        deleted += 2

    if deleted > 0:
        if _index_persistence:
            _index_persistence.schedule_save()
        safe_audit(
            kv,
            "forget",
            "mem::forget",
            deleted_mem_ids + deleted_obs_ids,
            {
                "memoryId": memory_id,
                "sessionId": session_id,
                "folderPath": folder_path_raw,
                "agentId": agent_id_raw,
                "deleted": deleted,
                "memoriesDeleted": len(deleted_mem_ids),
                "observationsDeleted": len(deleted_obs_ids),
                "sessionDeleted": deleted_session,
                "reason": "user-initiated forget"
            }
        )

        agent_id = data.get("agentId") or get_agent_id()
        commit_if_enabled(kv, f"Forget: memory_id={memory_id} folder_path={folder_path_raw}", agent_id)

    return {"success": True, "deleted": deleted}


# =====================================================================
# Prompt Context Compilation System
# =====================================================================

def estimate_tokens(text: str) -> int:
    return int(len(text) / 3)

def escape_xml_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

def context(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    session_id = data.get("sessionId")
    project = data.get("project")
    budget = data.get("budget") or int(os.getenv("TOKEN_BUDGET", "2000"))

    if not session_id or not project:
        raise ValueError("sessionId and project are required")

    blocks = []

    # 1. Pinned Slots
    pinned_slots = list_pinned_slots(kv, project)
    slot_content = render_pinned_context(pinned_slots)
    if slot_content:
        blocks.append({
            "type": "memory",
            "content": slot_content,
            "tokens": estimate_tokens(slot_content),
            "recency": int(time.time() * 1000)
        })

    # 2. Profile
    profile = kv.get(KV.profiles, project)
    if profile:
        profile_parts = []
        if profile.get("topConcepts"):
            profile_parts.append(
                "Concepts: " + ", ".join([c["concept"] for c in profile["topConcepts"][:8]])
            )
        if profile.get("topFiles"):
            profile_parts.append(
                "Key files: " + ", ".join([f["file"] for f in profile["topFiles"][:5]])
            )
        if profile.get("conventions"):
            profile_parts.append("Conventions: " + "; ".join(profile["conventions"]))
        if profile.get("commonErrors"):
            profile_parts.append("Common errors: " + "; ".join(profile["commonErrors"][:3]))
        
        if profile_parts:
            profile_content = f"## Project Profile\n" + "\n".join(profile_parts)
            blocks.append({
                "type": "memory",
                "content": profile_content,
                "tokens": estimate_tokens(profile_content),
                "recency": int(time.time() * 1000)
            })

    # 3. Lessons
    lessons = kv.list(KV.lessons)
    relevant_lessons = [
        l for l in lessons
        if not l.get("deleted") and (not l.get("project") or l["project"] == project)
    ]
    # Score lessons
    def lesson_score(l):
        factor = 1.5 if l.get("project") == project else 1.0
        return factor * l.get("confidence", 0.5)

    relevant_lessons.sort(key=lesson_score, reverse=True)
    relevant_lessons = relevant_lessons[:10]

    if relevant_lessons:
        items = []
        for l in relevant_lessons:
            desc = f"- ({l['confidence']:.2f}) {l['content']}"
            if l.get("context"):
                desc += f" — {l['context']}"
            items.append(desc)
        lessons_content = "## Lessons Learned\n" + "\n".join(items)
        blocks.append({
            "type": "memory",
            "content": lessons_content,
            "tokens": estimate_tokens(lessons_content),
            "recency": int(time.time() * 1000)
        })

    # 4. Sessions & Summaries
    all_sessions = kv.list(KV.sessions)
    sessions = [
        s for s in all_sessions
        if s.get("project") == project and s["id"] != session_id
    ]
    sessions.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
    sessions = sessions[:10]

    for s in sessions:
        summary = kv.get(KV.summaries, s["id"])
        if summary:
            content = f"## {summary.get('title', 'Session summary')}\n{summary.get('narrative', '')}\n" \
                      f"Decisions: {'; '.join(summary.get('keyDecisions', []))}\n" \
                      f"Files: {', '.join(summary.get('filesModified', []))}"
            blocks.append({
                "type": "summary",
                "content": content,
                "tokens": estimate_tokens(content),
                "recency": int(time.time() * 1000)
            })
        else:
            # Fallback to important observations
            obs_list = kv.list(KV.observations(s["id"]))
            important = [o for o in obs_list if o.get("title") and o.get("importance", 0) >= 5]
            if important:
                important.sort(key=lambda o: o.get("importance", 0), reverse=True)
                top = important[:5]
                items = [f"- [{o.get('type')}] {o.get('title')}: {o.get('narrative')}" for o in top]
                content = f"## Session {s['id'][:8]} ({s.get('startedAt')})\n" + "\n".join(items)
                blocks.append({
                    "type": "observation",
                    "content": content,
                    "tokens": estimate_tokens(content),
                    "recency": int(time.time() * 1000)
                })

    blocks.sort(key=lambda b: b.get("recency", 0), reverse=True)

    header = f'<agentmemory-context project="{escape_xml_attr(project)}">'
    footer = "</agentmemory-context>"
    used_tokens = estimate_tokens(header) + estimate_tokens(footer)

    selected = []
    for b in blocks:
        if used_tokens + b["tokens"] > budget:
            continue
        selected.append(b["content"])
        used_tokens += b["tokens"]

    if not selected:
        return {"context": "", "blocks": 0, "tokens": 0}

    res_context = f"{header}\n" + "\n\n".join(selected) + f"\n{footer}"
    return {"context": res_context, "blocks": len(selected), "tokens": used_tokens}

# =====================================================================
# Memory Slots System
# =====================================================================

DEFAULT_SLOTS = [
    {
        "label": "persona",
        "content": "",
        "sizeLimit": 1000,
        "description": "How the agent should see itself: role, tone, behavioural guidelines.",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "user_preferences",
        "content": "",
        "sizeLimit": 2000,
        "description": "Coding style, tool preferences, naming conventions, and other habits the user wants preserved across sessions.",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "tool_guidelines",
        "content": "",
        "sizeLimit": 1500,
        "description": "Rules the agent should follow when picking or sequencing tools (e.g. prefer X over Y, never run Z without confirmation).",
        "pinned": True,
        "readOnly": False,
        "scope": "global",
    },
    {
        "label": "project_context",
        "content": "",
        "sizeLimit": 3000,
        "description": "Architecture decisions, codebase conventions, build/test commands, and cross-cutting constraints for the current project.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "guidance",
        "content": "",
        "sizeLimit": 1500,
        "description": "Active advice for the next session: what to focus on, what to avoid, open risks.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "pending_items",
        "content": "",
        "sizeLimit": 2000,
        "description": "Unfinished work, explicit TODOs, and promises made but not yet delivered.",
        "pinned": True,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "session_patterns",
        "content": "",
        "sizeLimit": 1500,
        "description": "Recurring behaviours and common struggles observed across recent sessions.",
        "pinned": False,
        "readOnly": False,
        "scope": "project",
    },
    {
        "label": "self_notes",
        "content": "",
        "sizeLimit": 1500,
        "description": "Free-form notes the agent keeps for itself: hypotheses, dead ends, things to revisit.",
        "pinned": False,
        "readOnly": False,
        "scope": "project",
    },
]

def seed_defaults(kv: StateKV) -> None:
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for tmpl in DEFAULT_SLOTS:
        scope = tmpl["scope"]
        target = KV.globalSlots if scope == "global" else KV.slots
        existing = kv.get(target, tmpl["label"])
        if existing:
            continue
        slot = dict(tmpl)
        slot["createdAt"] = now
        slot["updatedAt"] = now
        kv.set(target, tmpl["label"], slot)

def list_pinned_slots(kv: StateKV, project: Optional[str] = None) -> List[Dict[str, Any]]:
    p_slots = kv.list(project_slots_scope(kv, project))
    g_slots = kv.list(KV.globalSlots)
    merged = {}
    for s in g_slots:
        merged[s["label"]] = s
    for s in p_slots:
        merged[s["label"]] = s
    pinned = [s for s in merged.values() if s.get("pinned") and s.get("content", "").strip()]
    pinned.sort(key=lambda s: s["label"])
    return pinned

def render_pinned_context(slots: List[Dict[str, Any]]) -> str:
    if not slots:
        return ""
    lines = ["# agentmemory pinned slots", ""]
    for s in slots:
        lines.append(f"## {s['label']}")
        lines.append(s["content"].strip())
        lines.append("")
    return "\n".join(lines)

def slot_list(kv: StateKV, project: Optional[str] = None) -> Dict[str, Any]:
    p_slots = kv.list(project_slots_scope(kv, project))
    g_slots = kv.list(KV.globalSlots)
    merged = {}
    for s in g_slots:
        merged[s["label"]] = s
    for s in p_slots:
        merged[s["label"]] = s
    slots = sorted(list(merged.values()), key=lambda s: s["label"])
    return {"success": True, "slots": slots}

def slot_get(kv: StateKV, label: str, project: Optional[str] = None) -> Dict[str, Any]:
    p_scope = project_slots_scope(kv, project)
    project_s = kv.get(p_scope, label)
    if project_s:
        return {"success": True, "slot": project_s, "scope": "project"}
    global_s = kv.get(KV.globalSlots, label)
    if global_s:
        return {"success": True, "slot": global_s, "scope": "global"}
    return {"success": False, "error": "slot not found"}

def slot_create(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    label = data.get("label")
    if not label or not re.match(r'^[a-z][a-z0-9_]*$', label):
        return {"success": False, "error": "label required (lowercase, starts with letter, [a-z0-9_])"}

    scope = data.get("scope") or "project"
    if scope not in ("project", "global"):
        return {"success": False, "error": "scope must be 'project' or 'global'"}

    limit = data.get("sizeLimit") or 2000
    if not isinstance(limit, int) or limit < 1 or limit > 20000:
        return {"success": False, "error": "sizeLimit must be an integer between 1 and 20000"}

    content = strip_private_data(data.get("content") or "")
    if len(content) > limit:
        return {"success": False, "error": f"content exceeds sizeLimit ({len(content)} > {limit})"}

    description = data.get("description") or ""
    pinned = data.get("pinned", True)
    project = data.get("project")

    target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
    existing = kv.get(target_kv, label)
    if existing:
        return {"success": False, "error": f"slot already exists in {scope} scope"}

    now = datetime.datetime.utcnow().isoformat() + "Z"
    slot = {
        "label": label,
        "content": content,
        "sizeLimit": limit,
        "description": description,
        "pinned": pinned,
        "readOnly": False,
        "scope": scope,
        "createdAt": now,
        "updatedAt": now,
    }
    kv.set(target_kv, label, slot)
    safe_audit(kv, "slot_create", "mem::slot-create", [label], {"scope": scope, "sizeLimit": limit, "pinned": pinned})
    
    # Commit to Dolt
    agent_id = data.get("agentId") or get_agent_id()
    commit_if_enabled(kv, f"Create slot: {label}", agent_id)

    return {"success": True, "slot": slot}

def slot_append(kv: StateKV, label: str, text: str, agent_id: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    content = slot.get("content") or ""
    sep = "\n" if content and not content.endswith("\n") else ""
    next_content = content + sep + strip_private_data(text)

    limit = slot.get("sizeLimit") or 2000
    if len(next_content) > limit:
        return {
            "success": False,
            "error": f"append would exceed sizeLimit ({len(next_content)} > {limit})",
            "currentSize": len(content),
            "sizeLimit": limit
        }

    slot["content"] = next_content
    slot["updatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"
    kv.set(target_kv, label, slot)

    safe_audit(kv, "slot_append", "mem::slot-append", [label], {"scope": scope, "added": len(text), "total": len(next_content)})
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Append slot: {label}", agent_id or get_agent_id())

    return {"success": True, "slot": slot, "size": len(next_content)}

def slot_replace(kv: StateKV, label: str, content: str, agent_id: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    content = strip_private_data(content)
    limit = slot.get("sizeLimit") or 2000
    if len(content) > limit:
        return {
            "success": False,
            "error": f"content exceeds sizeLimit ({len(content)} > {limit})",
            "sizeLimit": limit
        }

    before_len = len(slot.get("content") or "")
    slot["content"] = content
    slot["updatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"
    kv.set(target_kv, label, slot)

    safe_audit(kv, "slot_replace", "mem::slot-replace", [label], {"scope": scope, "before": before_len, "after": len(content)})
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Replace slot: {label}", agent_id or get_agent_id())

    return {"success": True, "slot": slot, "size": len(content)}

def slot_delete(kv: StateKV, label: str, agent_id: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    res = slot_get(kv, label, project)
    if not res.get("success"):
        return {"success": False, "error": "slot not found"}

    slot = res["slot"]
    scope = res["scope"]
    target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)

    if slot.get("readOnly"):
        return {"success": False, "error": "slot is read-only"}

    kv.delete(target_kv, label)
    safe_audit(kv, "slot_delete", "mem::slot-delete", [label], {"scope": scope, "size": len(slot.get("content") or "")})
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Delete slot: {label}", agent_id or get_agent_id())

    return {"success": True}


def slot_reflect(kv: StateKV, session_id: str, max_obs: int = 50) -> Dict[str, Any]:
    session = kv.get(KV.sessions, session_id)
    project = session.get("project") if session else None

    observations = kv.list(KV.observations(session_id))
    if not observations:
        return {"success": True, "applied": 0, "reason": "no observations for session"}

    recent = sorted(observations, key=lambda x: x.get("timestamp", ""), reverse=True)[:max_obs]

    pending_lines = []
    pattern_counts = {}
    files = set()

    for obs in recent:
        title = (obs.get("title") or "").lower()
        narrative = (obs.get("narrative") or "").lower()
        if "todo" in narrative or "todo" in title:
            pending_lines.append(f"- {obs.get('title') or obs['id']}")
        if obs.get("type") == "error":
            pattern_counts["errors"] = pattern_counts.get("errors", 0) + 1
        if obs.get("type") == "command_run":
            pattern_counts["commands"] = pattern_counts.get("commands", 0) + 1
        for f in obs.get("files") or []:
            files.add(f)

    applied = 0
    now = datetime.datetime.utcnow().isoformat() + "Z"

    if pending_lines:
        res = slot_get(kv, "pending_items", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = scopeKv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
            already = set((slot.get("content") or "").split("\n"))
            fresh = [l for l in pending_lines if l not in already]
            if fresh:
                sep = "\n" if slot.get("content") and not slot["content"].endswith("\n") else ""
                next_content = (slot.get("content") or "") + sep + "\n".join(fresh)
                limit = slot.get("sizeLimit") or 2000
                if len(next_content) > limit:
                    next_content = next_content[-limit:]
                slot["content"] = next_content
                slot["updatedAt"] = now
                kv.set(target_kv, "pending_items", slot)
                applied += 1

    if pattern_counts:
        res = slot_get(kv, "session_patterns", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
            summary = [f"last reflection: {now}"]
            for k, v in pattern_counts.items():
                summary.append(f"- {k}: {v} in last {len(recent)} observations")
            next_content = "\n".join(summary)
            limit = slot.get("sizeLimit") or 2000
            if len(next_content) > limit:
                next_content = next_content[:limit]
            slot["content"] = next_content
            slot["updatedAt"] = now
            kv.set(target_kv, "session_patterns", slot)
            applied += 1

    if files:
        res = slot_get(kv, "project_context", project)
        if res.get("success"):
            slot = res["slot"]
            scope = res["scope"]
            target_kv = KV.globalSlots if scope == "global" else project_slots_scope(kv, project)
            already = slot.get("content") or ""
            fresh = [f for f in files if f not in already][:20]
            if fresh:
                header_line = "Files touched in recent sessions:" if not already else ""
                sep = "\n" if already and not already.endswith("\n") else ""
                lines = [already]
                if header_line:
                    lines.append(header_line)
                for f in fresh:
                    lines.append(f"- {f}")
                next_content = sep.join([l for l in lines if l])
                limit = slot.get("sizeLimit") or 2000
                if len(next_content) > limit:
                    next_content = next_content[-limit:]
                slot["content"] = next_content
                slot["updatedAt"] = now
                kv.set(target_kv, "project_context", slot)
                applied += 1

    if applied > 0:
        safe_audit(kv, "slot_reflect", "mem::slot-reflect", [session_id], {"observationCount": len(recent), "slotsUpdated": applied})
        commit_if_enabled(kv, f"Slot reflect: updated {applied} slots in session {session_id[:8]}", "system")

    return {"success": True, "applied": applied, "observationsReviewed": len(recent)}


# =====================================================================
# Lessons Learned System
# =====================================================================

def reinforce_lesson(lesson: Dict[str, Any]) -> None:
    now = datetime.datetime.utcnow().isoformat() + "Z"
    lesson["reinforcements"] = lesson.get("reinforcements", 0) + 1
    conf = lesson.get("confidence", 0.5)
    lesson["confidence"] = min(1.0, conf + 0.1 * (1 - conf))
    lesson["lastReinforcedAt"] = now
    lesson["updatedAt"] = now

def lesson_save(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    content = data.get("content")
    if not content or not content.strip():
        return {"success": False, "error": "content is required"}
    content = strip_private_data(content)
    context_str = strip_private_data(data.get("context") or "")

    agent_id = data.get("agentId") or get_agent_id()
    fp = fingerprint_id("lsn", content)
    existing = kv.get(KV.lessons, fp)

    if existing and not existing.get("deleted"):
        reinforce_lesson(existing)
        if context_str and not existing.get("context"):
            existing["context"] = context_str
        kv.set(KV.lessons, existing["id"], existing)
        safe_audit(kv, "lesson_strengthen", "mem::lesson-save", [existing["id"]])
        
        # Commit to Dolt
        commit_if_enabled(kv, f"Strengthen lesson: {existing.get('content', '')[:60]}", agent_id)
        
        return {"success": True, "action": "strengthened", "lesson": existing}

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        confidence = 0.5

    now = datetime.datetime.utcnow().isoformat() + "Z"
    lesson = {
        "id": fp,
        "content": content.strip(),
        "context": context_str.strip(),
        "confidence": confidence,
        "reinforcements": 0,
        "source": data.get("source") or "manual",
        "sourceIds": data.get("sourceIds") or [],
        "project": data.get("project"),
        "tags": data.get("tags") or [],
        "createdAt": now,
        "updatedAt": now,
        "decayRate": 0.05,
    }
    kv.set(KV.lessons, lesson["id"], lesson)
    safe_audit(kv, "lesson_save", "mem::lesson-save", [lesson["id"]])
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Create lesson: {lesson['content'][:60]}", agent_id)

    return {"success": True, "action": "created", "lesson": lesson}


def lesson_list(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    limit = data.get("limit") or 50
    min_confidence = data.get("minConfidence") or 0.0
    all_lessons = kv.list(KV.lessons)

    lessons = [
        l for l in all_lessons
        if not l.get("deleted") and l.get("confidence", 0.5) >= min_confidence
    ]

    project = data.get("project")
    if project:
        lessons = [l for l in lessons if l.get("project") == project]
    source = data.get("source")
    if source:
        lessons = [l for l in lessons if l.get("source") == source]

    lessons.sort(key=lambda x: x.get("confidence", 0.5), reverse=True)
    return {"success": True, "lessons": lessons[:limit]}

def lesson_recall(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    query = data.get("query")
    if not query or not query.strip():
        return {"success": False, "error": "query is required"}

    query_lower = query.lower()
    min_confidence = data.get("minConfidence") or 0.1
    limit = data.get("limit") or 10

    all_lessons = kv.list(KV.lessons)
    lessons = [
        l for l in all_lessons
        if not l.get("deleted") and l.get("confidence", 0.5) >= min_confidence
    ]

    project = data.get("project")
    if project:
        lessons = [l for l in lessons if l.get("project") == project]

    scored = []
    terms = [t for t in query_lower.split() if len(t) > 1]

    for l in lessons:
        text = f"{l.get('content', '')} {l.get('context', '')} {' '.join(l.get('tags') or [])}".lower()
        match_count = sum(1 for t in terms if t in text)
        if match_count == 0:
            continue

        relevance = match_count / len(terms)
        baseline = l.get("lastReinforcedAt") or l.get("createdAt")
        import dateutil.parser
        dt = dateutil.parser.parse(baseline)
        days = (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - dt.replace(tzinfo=datetime.timezone.utc)).total_seconds() / (3600 * 24)
        recency_boost = 1 / (1 + days * 0.01)
        score = l.get("confidence", 0.5) * relevance * recency_boost
        scored.append({"lesson": l, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    results = []
    for s in scored[:limit]:
        item = dict(s["lesson"])
        item["score"] = round(s["score"], 3)
        results.append(item)

    safe_audit(kv, "lesson_recall", "mem::lesson-recall", [], {"query": query, "resultCount": len(results)})
    return {"success": True, "lessons": results}

def lesson_strengthen(kv: StateKV, lesson_id: str) -> Dict[str, Any]:
    lesson = kv.get(KV.lessons, lesson_id)
    if not lesson or lesson.get("deleted"):
        return {"success": False, "error": "lesson not found"}

    reinforce_lesson(lesson)
    kv.set(KV.lessons, lesson["id"], lesson)
    safe_audit(kv, "lesson_strengthen", "mem::lesson-strengthen", [lesson["id"]])
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Strengthen lesson: {lesson.get('content', '')[:60]}", get_agent_id())
    
    return {"success": True, "lesson": lesson}

def lesson_decay_sweep(kv: StateKV) -> Dict[str, Any]:
    all_lessons = kv.list(KV.lessons)
    decayed = 0
    soft_deleted = 0
    now = datetime.datetime.utcnow()
    timestamp = now.isoformat() + "Z"

    for l in all_lessons:
        if l.get("deleted"):
            continue
        baseline_str = l.get("lastDecayedAt") or l.get("lastReinforcedAt") or l["createdAt"]
        import dateutil.parser
        dt = dateutil.parser.parse(baseline_str)
        weeks = (now.replace(tzinfo=datetime.timezone.utc) - dt.replace(tzinfo=datetime.timezone.utc)).total_seconds() / (3600 * 24 * 7)
        if weeks < 1.0:
            continue

        decay = l.get("decayRate", 0.05) * weeks
        new_conf = max(0.05, l.get("confidence", 0.5) - decay)
        
        if new_conf != l.get("confidence"):
            before = l.get("confidence", 0.5)
            l["confidence"] = round(new_conf, 3)
            l["lastDecayedAt"] = timestamp
            l["updatedAt"] = timestamp

            if l["confidence"] <= 0.1 and l.get("reinforcements", 0) == 0:
                l["deleted"] = True
                soft_deleted += 1
            else:
                decayed += 1

            kv.set(KV.lessons, l["id"], l)
            safe_audit(kv, "lesson_strengthen", "mem::lesson-decay-sweep", [l["id"]], {
                "action": "soft-delete" if l.get("deleted") else "decay",
                "actor": "system",
                "reason": "decay-sweep",
                "before": {"confidence": before, "deleted": False},
                "after": {"confidence": l["confidence"], "deleted": bool(l.get("deleted"))}
            })

    if decayed > 0 or soft_deleted > 0:
        commit_if_enabled(kv, f"Lesson decay sweep: decayed {decayed}, soft-deleted {soft_deleted}", "system")

    return {"success": True, "decayed": decayed, "softDeleted": soft_deleted, "total": len(all_lessons)}


# =====================================================================
# Database Rebuilder (Index Bootstrapper)
# =====================================================================

def rebuild_index(kv: StateKV) -> int:
    _bm25_index.clear()
    if _vector_index:
        _vector_index.clear()

    total_indexed = 0

    # ---- Path A: folder-based observations (new schema) ----
    folder_pairs = kv.list(KV.folders)
    for entry in folder_pairs:
        fp = entry.get("folderPath")
        aid = entry.get("agentId")
        if not fp or not aid:
            continue
        obs_list = kv.list(KV.folder_obs(fp, aid))
        for obs in obs_list:
            if not obs.get("id"):
                continue
            # Populate coordinate lookup index
            kv.set(KV.obs_lookup, obs["id"], {"folderPath": fp, "agentId": aid})
            
            _bm25_index.add(obs)
            comb_text = (obs.get("title") or "") + " " + (obs.get("text") or "")
            vector_index_add_guarded(obs["id"], fp, comb_text.strip(), {"kind": "folder_observation", "logId": obs["id"]})
            total_indexed += 1

    # ---- Path B: session-based observations (legacy schema — kept for old data) ----
    try:
        sessions = kv.list(KV.sessions)
        for sess in sessions:
            sid = sess.get("id")
            if not sid:
                continue
            obs_list = kv.list(KV.observations(sid))
            for obs in obs_list:
                # Only index compressed (non-raw) observations
                if obs.get("title") and obs.get("narrative"):
                    # Skip if already indexed via folder path (same obs id)
                    if _bm25_index.has(obs["id"]):
                        continue
                    _bm25_index.add(obs)
                    comb_text = obs["title"] + " " + obs["narrative"]
                    vector_index_add_guarded(obs["id"], sid, comb_text, {"kind": "observation", "logId": obs["id"]})
                    total_indexed += 1
    except Exception as e:
        print(f"[rebuild_index] session-based backfill skipped: {e}")

    # ---- Backfill BM25 with global memories (both schemas) ----
    memories = kv.list(KV.memories)
    for mem in memories:
        if mem.get("isLatest") is False:
            continue
        if not mem.get("title") or not mem.get("content"):
            continue
        converted = memory_to_observation(mem)
        _bm25_index.add(converted)
        comb_text = mem["title"] + " " + mem["content"]
        vector_index_add_guarded(mem["id"], "memory", comb_text, {"kind": "memory", "logId": mem["id"]})
        total_indexed += 1

    if _index_persistence and total_indexed > 0:
        _index_persistence.schedule_save()

    return total_indexed

# =====================================================================
# Advanced Function Stubs / CRUD Operations
# =====================================================================

def list_sessions(kv: StateKV) -> List[Dict[str, Any]]:
    sessions = kv.list(KV.sessions)
    for s in sessions:
        sid = s.get("id")
        if sid:
            summary = kv.get(KV.summaries, sid)
            if summary:
                s["title"] = summary.get("title")
                s["summary"] = summary.get("narrative")
    sessions.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
    return sessions

def get_session(kv: StateKV, session_id: str) -> Optional[Dict[str, Any]]:
    s = kv.get(KV.sessions, session_id)
    if s:
        summary = kv.get(KV.summaries, session_id)
        if summary:
            s["title"] = summary.get("title")
            s["summary"] = summary.get("narrative")
    return s

def create_session(kv: StateKV, session: Dict[str, Any]) -> Dict[str, Any]:
    auto_complete_old_active_sessions(kv, session["id"], project=session.get("project"), agent_id=session.get("agentId"))
    kv.set(KV.sessions, session["id"], session)
    return session

def end_session(kv: StateKV, session_id: str) -> bool:
    now = datetime.datetime.utcnow().isoformat() + "Z"
    kv.update(KV.sessions, session_id, [
        {"type": "set", "path": "endedAt", "value": now},
        {"type": "set", "path": "status", "value": "completed"}
    ])
    return True

def timeline(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    # Simple timeline query returning observations sorted by timestamp
    anchor = data.get("anchor")
    project = data.get("project")
    session_id = data.get("sessionId")
    before = data.get("before") or 10
    after = data.get("after") or 10

    sessions = kv.list(KV.sessions)
    if session_id:
        sessions = [s for s in sessions if s.get("id") == session_id]
    elif project:
        sessions = [s for s in sessions if s.get("project") == project]

    all_obs = []
    for s in sessions:
        all_obs.extend(kv.list(KV.observations(s["id"])))

    # sort by timestamp
    all_obs.sort(key=lambda x: x.get("timestamp", ""))
    
    anchor_idx = -1
    for idx, obs in enumerate(all_obs):
        if obs["id"] == anchor or obs.get("timestamp", "") >= (anchor or ""):
            anchor_idx = idx
            break

    if anchor_idx == -1:
        anchor_idx = len(all_obs) // 2

    start = max(0, anchor_idx - before)
    end = min(len(all_obs), anchor_idx + after + 1)

    return {
        "success": True,
        "observations": all_obs[start:end],
        "anchorIndex": anchor_idx - start
    }

def get_project_profile(kv: StateKV, project: str) -> Dict[str, Any]:
    prof = kv.get(KV.profiles, project)
    if not prof:
        prof = {
            "project": project,
            "topConcepts": [],
            "topFiles": [],
            "conventions": [],
            "commonErrors": [],
            "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
        }
    if not prof.get("topConcepts") and not prof.get("topFiles"):
        prof = build_project_profile(kv, project)
    return prof

def build_project_profile(kv: StateKV, project: str) -> Dict[str, Any]:
    prof = kv.get(KV.profiles, project)
    if not prof:
        prof = {
            "project": project,
            "topConcepts": [],
            "topFiles": [],
            "conventions": [],
            "commonErrors": [],
            "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
        }

    # Stored profile may lack topConcepts/topFiles — compute from observations + memories if empty
    if not prof.get("topConcepts") and not prof.get("topFiles"):
        import re as _re, json as _j, os.path as _osp
        from collections import Counter
        sessions = kv.list(KV.sessions)
        project_sessions = [s for s in sessions if s.get("project") == project]
        concept_counts = Counter()
        file_counts = Counter()

        def _harvest_file(path, fc, cc):
            if not isinstance(path, str) or not path:
                return
            fc[path] += 1
            parts = _re.split(r'[\\/]', path)
            fname = parts[-1] if parts else ""
            skip = {"tmp", "temp", "claude", "appdata", "local", "users", "windows"}
            for part in parts[:-1]:
                p = part.lower().strip()
                if p and len(p) > 2 and p not in skip and not _re.match(r'^[a-z]:|^\.|^--', p):
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
                for c in (o.get("concepts") or []):
                    if isinstance(c, str) and c:
                        concept_counts[c] += 1
                for f in (o.get("files") or []):
                    _harvest_file(f, file_counts, concept_counts)
                tn = o.get("toolName")
                if tn:
                    concept_counts[tn] += 1
                ti = o.get("toolInput")
                if isinstance(ti, str):
                    try: ti = _j.loads(ti)
                    except Exception: ti = {}
                if isinstance(ti, dict):
                    for fk in ("path", "file_path", "file", "filename"):
                        _harvest_file(ti.get(fk, ""), file_counts, concept_counts)
                narr = o.get("narrative") or o.get("raw") or ""
                if isinstance(narr, str) and narr.startswith("{"):
                    try:
                        nd = _j.loads(narr)
                        if isinstance(nd, dict):
                            tn2 = nd.get("toolName") or nd.get("tool_name")
                            if tn2: concept_counts[tn2] += 1
                            for fk in ("path", "file_path", "file", "filename"):
                                _harvest_file(nd.get(fk, ""), file_counts, concept_counts)
                    except Exception:
                        pass

        # memories for this project
        for m in kv.list(KV.memories):
            if m.get("project") == project:
                for c in (m.get("concepts") or []):
                    if c: concept_counts[c] += 1
                for f in (m.get("files") or []):
                    _harvest_file(f, file_counts, concept_counts)

        prof["topConcepts"] = [{"concept": c, "frequency": n} for c, n in concept_counts.most_common(20)]
        prof["topFiles"] = [{"file": f, "frequency": n} for f, n in file_counts.most_common(20)]
        prof["sessionCount"] = len(project_sessions)

    return prof

def export_data(kv: StateKV, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if data is None:
        data = {}

    exported_at = datetime.datetime.utcnow().isoformat() + "Z"

    # ---- v2 folder-based export (primary path) ----
    folder_pairs = kv.list(KV.folders)
    folders_export = []
    for entry in folder_pairs:
        fp = entry.get("folderPath")
        aid = entry.get("agentId")
        if not fp or not aid:
            continue
        meta = kv.get(KV.folder_meta(fp, aid), "meta") or {
            "folderPath": fp,
            "agentId": aid,
            "lastUpdated": entry.get("lastUpdated", ""),
            "obsCount": entry.get("obsCount", 0),
        }
        observations = kv.list(KV.folder_obs(fp, aid))
        folders_export.append({
            "folderPath": fp,
            "agentId": aid,
            "meta": meta,
            "observations": observations,
        })

    memories = kv.list(KV.memories)

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
    sessions = kv.list(KV.sessions)
    migrated_sessions = 0
    migrated_observations = 0
    errors = []

    for session in sessions:
        session_id = session.get('id')
        if not session_id:
            continue
        try:
            fp_raw = session.get('cwd') or session.get('project') or 'unknown'
            aid = (session.get('agentId') or 'unknown').strip()[:_MAX_PATH_LEN]
            try:
                fp = normalize_folder_path(fp_raw)
            except ValueError:
                fp = 'unknown'

            obs_list = kv.list(KV.observations(session_id))
            session_obs_count = 0
            for obs in obs_list:
                obs_id = obs.get('id', '')
                if obs_id.endswith(':raw'):
                    continue
                folder_obs = {
                    'id': obs_id,
                    'folderPath': fp,
                    'agentId': aid,
                    'timestamp': obs.get('timestamp', ''),
                    'text': obs.get('narrative') or obs.get('raw') or obs.get('title') or '',
                    'type': obs.get('type', 'other'),
                    'title': obs.get('title', ''),
                    'concepts': obs.get('concepts') or [],
                    'files': obs.get('files') or [],
                    'importance': obs.get('importance', 5),
                }
                if isinstance(folder_obs['text'], dict):
                    import json as _json
                    folder_obs['text'] = _json.dumps(folder_obs['text'])[:4000]
                folder_obs['text'] = str(folder_obs['text'])[:4000]

                if not dry_run:
                    kv.set(KV.folder_obs(fp, aid), obs_id, folder_obs)
                    kv.set(KV.obs_lookup, obs_id, {
                        'folderPath': fp,
                        'agentId': aid,
                    })
                session_obs_count += 1
                migrated_observations += 1

            if not dry_run and session_obs_count > 0:
                meta_scope = KV.folder_meta(fp, aid)
                meta = kv.get(meta_scope, 'meta') or {
                    'folderPath': fp, 'agentId': aid, 'obsCount': 0,
                    'lastUpdated': session.get('updatedAt', ''), 'summary': None,
                }
                meta['obsCount'] = meta.get('obsCount', 0) + session_obs_count
                meta['lastUpdated'] = session.get('updatedAt', '') or meta['lastUpdated']
                kv.set(meta_scope, 'meta', meta)

                index_key = f'{fp}:{aid}'
                kv.set(KV.folders, index_key, {
                    'folderPath': fp,
                    'agentId': aid,
                    'lastUpdated': meta['lastUpdated'],
                    'obsCount': meta['obsCount'],
                })

            migrated_sessions += 1
        except Exception as e:
            errors.append({'sessionId': session_id, 'error': str(e)})

    return {
        'migrated_sessions': migrated_sessions,
        'migrated_observations': migrated_observations,
        'errors': errors,
        'dry_run': dry_run,
    }


def set_project_profile(kv: StateKV, project: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    profile["updatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"
    kv.set(KV.profiles, project, profile)
    
    # Commit to Dolt
    commit_if_enabled(kv, f"Set project profile for {project}", get_agent_id())
    
    return profile

def get_relations(kv: StateKV) -> List[Dict[str, Any]]:
    return kv.list(KV.relations)

def add_relation(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    rel = {
        "id": generate_id("rel"),
        "sourceId": data["sourceId"],
        "targetId": data["targetId"],
        "type": data["type"],
        "createdAt": datetime.datetime.utcnow().isoformat() + "Z"
    }
    kv.set(KV.relations, rel["id"], rel)
    
    # Commit to Dolt
    agent_id = data.get("agentId") or get_agent_id()
    commit_if_enabled(kv, f"Add relation {rel['type']} between {rel['sourceId']} and {rel['targetId']}", agent_id)

    return rel

def evolve_memory(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    # Update memory content and create a new version
    mem_id = data["memoryId"]
    new_content = data["newContent"]
    new_title = data.get("newTitle")

    existing = kv.get(KV.memories, mem_id)
    if not existing:
        raise ValueError("Memory not found")

    existing["isLatest"] = False
    kv.set(KV.memories, existing["id"], existing)

    now = datetime.datetime.utcnow().isoformat() + "Z"
    new_mem = dict(existing)
    new_mem["id"] = generate_id("mem")
    new_mem["content"] = new_content
    if new_title:
        new_mem["title"] = new_title
    else:
        new_mem["title"] = new_content[:80]
    new_mem["version"] = existing.get("version", 1) + 1
    new_mem["parentId"] = existing["id"]
    new_mem["supersedes"] = [existing["id"]]
    new_mem["createdAt"] = now
    new_mem["updatedAt"] = now
    new_mem["isLatest"] = True

    kv.set(KV.memories, new_mem["id"], new_mem)

    # Re-index
    try:
        _bm25_index.add(memory_to_observation(new_mem))
        _bm25_index.remove(existing["id"])
    except Exception:
        pass

    comb_text = new_mem["title"] + " " + new_mem["content"]
    vector_index_add_guarded(new_mem["id"], "memory", comb_text, {"kind": "memory", "logId": new_mem["id"]})
    if _vector_index:
        _vector_index.remove(existing["id"])

    if _index_persistence:
        _index_persistence.schedule_save()

    # Commit to Dolt
    agent_id = data.get("agentId") or get_agent_id() or new_mem.get("agentId")
    commit_if_enabled(kv, f"Evolve memory {new_mem['id']} (v{new_mem['version']}): {new_mem['title']}", agent_id)

    return {"success": True, "memory": new_mem}

def auto_forget(kv: StateKV, dry_run: bool = False) -> Dict[str, Any]:
    now_dt = datetime.datetime.utcnow()
    now_str = now_dt.isoformat() + "Z"
    evicted_memories = []
    evicted_observations = []

    # 1. Evict expired memories
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
                print(f"[auto_forget] Failed to parse forgetAfter '{forget_after}': {e}")

    # 2. Evict low-value old observations (importance <= 2, age > 180 days)
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

    if not dry_run:
        for mem_id in evicted_memories:
            mem = kv.get(KV.memories, mem_id)
            kv.delete(KV.memories, mem_id)
            if mem and mem.get("imageRef"):
                ref = mem["imageRef"]
                refs = kv.get(KV.imageRefs, ref) or 0
                if refs > 0:
                    kv.set(KV.imageRefs, ref, refs - 1)
            _bm25_index.remove(mem_id)
            if _vector_index:
                _vector_index.remove(mem_id)

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
            
            _bm25_index.remove(base_oid)
            _bm25_index.remove(f"{base_oid}:raw")
            if _vector_index:
                _vector_index.remove(base_oid)
                _vector_index.remove(f"{base_oid}:raw")

        if evicted_memories or evicted_observations:
            if _index_persistence:
                _index_persistence.schedule_save()
            safe_audit(
                kv,
                "auto_forget",
                "mem::auto_forget",
                evicted_memories + [oid for _, oid in evicted_observations],
                {
                    "evictedMemoriesCount": len(evicted_memories),
                    "evictedObservationsCount": len(evicted_observations),
                    "dryRun": False
                }
            )
            commit_if_enabled(kv, f"Auto forget: evicted {len(evicted_memories)} memories, {len(evicted_observations)} observations", "system")

    return {
        "success": True,
        "evictedMemories": evicted_memories,
        "evictedObservations": [oid for _, oid in evicted_observations],
        "evicted": len(evicted_memories) + len(evicted_observations),
        "dryRun": dry_run
    }

def health_check(kv: StateKV) -> Dict[str, Any]:
    db_status = "connected"
    try:
        kv._get_conn()  # connection stays open per-thread (A3.1)
    except Exception:
        db_status = "disconnected"

    # ---- Folder-based counts ----
    folder_count = 0
    agent_count = 0
    pair_count = 0
    observation_count = 0
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
    try:
        memory_count = len(kv.list(KV.memories))
    except Exception:
        pass

    bm25_index_size = 0
    try:
        bm25_index_size = _bm25_index.size
    except Exception:
        pass

    vector_index_size = 0
    try:
        if _vector_index:
            vector_index_size = _vector_index.size
    except Exception:
        pass

    # C4.2: Read sync state written by sync.py
    sync_status = "never"
    last_sync_at = None
    db_size_bytes = 0
    wal_size_bytes = 0
    try:
        sync_state_path = os.path.join(os.path.expanduser("~"), ".agentmemory", ".sync_state")
        if os.path.exists(sync_state_path):
            with open(sync_state_path, "r", encoding="utf-8") as _sf:
                _sync = json.loads(_sf.read())
            sync_status = _sync.get("sync_status", "never")
            last_sync_at = _sync.get("last_sync_at")
    except Exception:
        pass

    # A3.3: DB file sizes
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
        "dbPath": kv.db_path,
        "dbSizeBytes": db_size_bytes,
        "walSizeBytes": wal_size_bytes,
        "syncStatus": sync_status,
        "lastSyncAt": last_sync_at,
    }

def strip_xml_wrappers(raw: str) -> str:
    if not raw:
        return ""
    cleaned = raw.strip()
    cleaned = re.sub(r'```xml\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'```', '', cleaned)
    cleaned = cleaned.strip()
    root_match = re.search(r'(<[a-zA-Z_][a-zA-Z0-9_-]*>[\s\S]*<\/[a-zA-Z_][a-zA-Z0-9_-]*>)', cleaned)
    if root_match:
        return root_match.group(1).strip()
    return cleaned

def get_xml_tag(text: str, tag: str) -> Optional[str]:
    cleaned = strip_xml_wrappers(text)
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, cleaned, re.DOTALL)
    return match.group(1).strip() if match else None

def get_xml_children(text: str, parent_tag: str, child_tag: str) -> List[str]:
    parent_content = get_xml_tag(text, parent_tag)
    if not parent_content:
        return []
    pattern = rf"<{child_tag}>(.*?)</{child_tag}>"
    return [m.strip() for m in re.findall(pattern, parent_content, re.DOTALL)]

def generate_content(system_instruction: str, prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("No Gemini/Google API key found")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "systemInstruction": {
            "parts": [
                {"text": system_instruction}
            ]
        },
        "generationConfig": {
            "temperature": 0.2
        }
    }
    
    req_data = json.dumps(payload).encode("utf-8")
    import urllib.request
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60.0) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            
        candidates = resp_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini generateContent returned no candidates")
        
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Gemini generateContent candidate content had no parts")
            
        return parts[0].get("text", "")
    except Exception as e:
        raise RuntimeError(f"Gemini generateContent call failed: {e}")

def summarize(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    session_id = data.get("sessionId")
    if not session_id:
        return {"success": False, "error": "sessionId is required"}
    
    session = kv.get(KV.sessions, session_id)
    if not session:
        return {"success": False, "error": "session_not_found"}
        
    observations = kv.list(KV.observations(session_id))
    compressed = [o for o in observations if o.get("title")]
    if not compressed:
        return {"success": False, "error": "no_observations"}
        
    SUMMARY_SYSTEM = """You are a session summarization assistant. Your job is to read all raw tool executions and outcomes from a coding session and produce a high-fidelity summary.
    
    Output XML:
    <summary>
      <title>Concise title summarizing the session</title>
      <narrative>1-2 paragraphs of narrative describing what was done, what succeeded, and what failed</narrative>
      <decisions>
        <decision>Architectural decision, key insight, or choice made</decision>
      </decisions>
      <files>
        <file>path/to/modified/file</file>
      </files>
      <concepts>
        <concept>important concept, library, tool, or command used</concept>
      </concepts>
    </summary>"""

    chunk_size = 400
    chunks = [compressed[i:i + chunk_size] for i in range(0, len(compressed), chunk_size)]
    
    partial_summaries = []
    for idx, chunk in enumerate(chunks):
        obs_text = ""
        for o in chunk:
            obs_text += f"[{o.get('type')}] {o.get('title')}\n{o.get('narrative') or ''}\nFiles: {', '.join(o.get('files') or [])}\n\n"
        
        prompt = f"Summarize this chunk {idx+1}/{len(chunks)} of observations:\n\n{obs_text}"
        try:
            response = generate_content(SUMMARY_SYSTEM, prompt)
            cleaned = strip_xml_wrappers(response)
            title = get_xml_tag(cleaned, "title")
            if not title:
                continue
            partial_summaries.append({
                "title": title,
                "narrative": get_xml_tag(cleaned, "narrative") or "",
                "keyDecisions": get_xml_children(cleaned, "decisions", "decision"),
                "filesModified": get_xml_children(cleaned, "files", "file"),
                "concepts": get_xml_children(cleaned, "concepts", "concept"),
            })
        except Exception as e:
            last_error = str(e)
            print(f"[summarize] Chunk {idx+1} failed: {e}")
            
    if not partial_summaries:
        return {"success": False, "error": f"No chunks summarized successfully. Last error: {last_error}"}
        
    if len(partial_summaries) == 1:
        final_summary = {
            "sessionId": session_id,
            "project": session.get("project"),
            "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
            "title": partial_summaries[0]["title"],
            "narrative": partial_summaries[0]["narrative"],
            "keyDecisions": partial_summaries[0]["keyDecisions"],
            "filesModified": partial_summaries[0]["filesModified"],
            "concepts": partial_summaries[0]["concepts"],
            "observationCount": len(compressed)
        }
    else:
        REDUCE_SYSTEM = """You are a session summarization reducer. Reduce multiple partial chunk summaries into a single final summary.
        
        Output XML:
        <summary>
          <title>Concise final title summarizing the entire session</title>
          <narrative>Comprehensive narrative describing what was done, what succeeded, and what failed</narrative>
          <decisions>
            <decision>Architectural decision, key insight, or choice made</decision>
          </decisions>
          <files>
            <file>path/to/modified/file</file>
          </files>
          <concepts>
            <concept>important concept, library, tool, or command used</concept>
          </concepts>
        </summary>"""
        
        reduce_prompt = "Reduce these partial summaries:\n\n"
        for idx, ps in enumerate(partial_summaries):
            reduce_prompt += f"[Chunk {idx+1}]\nTitle: {ps['title']}\nNarrative: {ps['narrative']}\nDecisions: {', '.join(ps['keyDecisions'])}\nFiles: {', '.join(ps['filesModified'])}\nConcepts: {', '.join(ps['concepts'])}\n\n"
            
        try:
            response = generate_content(REDUCE_SYSTEM, reduce_prompt)
            cleaned = strip_xml_wrappers(response)
            final_summary = {
                "sessionId": session_id,
                "project": session.get("project"),
                "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
                "title": get_xml_tag(cleaned, "title") or partial_summaries[0]["title"],
                "narrative": get_xml_tag(cleaned, "narrative") or "",
                "keyDecisions": get_xml_children(cleaned, "decisions", "decision"),
                "filesModified": get_xml_children(cleaned, "files", "file"),
                "concepts": get_xml_children(cleaned, "concepts", "concept"),
                "observationCount": len(compressed)
            }
        except Exception as e:
            return {"success": False, "error": f"Reduction failed: {e}"}
            
    kv.set(KV.summaries, session_id, final_summary)
    
    session = kv.get(KV.sessions, session_id)
    if session:
        session["title"] = final_summary["title"]
        session["summary"] = final_summary["narrative"]
        kv.set(KV.sessions, session_id, session)

    safe_audit(kv, "compress", "mem::summarize", [session_id], {
        "title": final_summary["title"],
        "observationCount": len(compressed)
    })
    
    return {"success": True, "summary": final_summary}

def consolidate(kv: StateKV, project: Optional[str] = None, min_observations: int = 10) -> Dict[str, Any]:
    sessions = list_sessions(kv)
    if project:
        sessions = [s for s in sessions if s.get("project") == project]
        
    all_obs = []
    for s in sessions:
        obs_list = kv.list(KV.observations(s["id"]))
        for o in obs_list:
            if o.get("title") and o.get("importance", 5) >= 5:
                all_obs.append((o, s["id"]))
                
    if len(all_obs) < min_observations:
        return {"consolidated": 0, "reason": "insufficient_observations", "success": True}
        
    # Group observations by concepts
    concept_groups = {}
    for obs, sid in all_obs:
        concepts = obs.get("concepts") or []
        for c in concepts:
            key = c.lower().strip()
            if not key:
                continue
            if key not in concept_groups:
                concept_groups[key] = []
            concept_groups[key].append((obs, sid))
            
    # Sort groups that have >= 3 observations by size descending
    sorted_groups = sorted(
        [(k, g) for k, g in concept_groups.items() if len(g) >= 3],
        key=lambda x: len(x[1]),
        reverse=True
    )
    
    consolidated_count = 0
    existing_memories = kv.list(KV.memories)
    
    MAX_LLM_CALLS = 10
    llm_calls = 0
    
    # Prompt templates
    CONSOLIDATION_SYSTEM = """You are a memory consolidation engine. Given a set of related observations from coding sessions, synthesize them into a single long-term memory.
    
    Output XML:
    <memory>
      <type>pattern|preference|architecture|bug|workflow|fact</type>
      <title>Concise memory title (max 80 chars)</title>
      <content>2-4 sentence description of the learned insight</content>
      <concepts>
        <concept>key term</concept>
      </concepts>
      <files>
        <file>relevant/file/path</file>
      </files>
      <strength>1-10 how confident/important this memory is</strength>
    </memory>"""
    
    for concept, obs_group in sorted_groups:
        if llm_calls >= MAX_LLM_CALLS:
            break
            
        # Get top 8 by importance
        top = sorted(obs_group, key=lambda x: x[0].get("importance", 5), reverse=True)[:8]
        session_ids = list(set([x[1] for x in top]))
        obs_ids = list(set([x[0]["id"] for x in top]))
        
        prompt_parts = []
        for obs, sid in top:
            prompt_parts.append(f"[{obs.get('type')}] {obs.get('title')}\n{obs.get('narrative') or ''}\nFiles: {', '.join(obs.get('files') or [])}\nImportance: {obs.get('importance', 5)}")
        obs_prompt = "\n\n".join(prompt_parts)
        
        try:
            response = generate_content(CONSOLIDATION_SYSTEM, f"Concept: \"{concept}\"\n\nObservations:\n{obs_prompt}")
            llm_calls += 1
            
            cleaned = strip_xml_wrappers(response)
            m_type = get_xml_tag(cleaned, "type") or "fact"
            m_title = get_xml_tag(cleaned, "title")
            m_content = get_xml_tag(cleaned, "content")
            
            if not m_title or not m_content:
                continue
                
            m_strength_str = get_xml_tag(cleaned, "strength") or "5"
            try:
                m_strength = max(1, min(10, int(m_strength_str)))
            except Exception:
                m_strength = 5
                
            concepts_list = get_xml_children(cleaned, "concepts", "concept")
            files_list = get_xml_children(cleaned, "files", "file")
            
            now = datetime.datetime.utcnow().isoformat() + "Z"
            
            # Find existing memory with same title
            existing_match = None
            for mem in existing_memories:
                if mem.get("title", "").lower() == m_title.lower() and mem.get("isLatest") is not False:
                    if not project or not mem.get("project") or mem.get("project") == project:
                        existing_match = mem
                        break
                        
            if existing_match:
                existing_match["isLatest"] = False
                kv.set(KV.memories, existing_match["id"], existing_match)
                
                evolved = {
                    "id": generate_id("mem"),
                    "createdAt": now,
                    "updatedAt": now,
                    "type": m_type,
                    "title": m_title,
                    "content": m_content,
                    "concepts": concepts_list,
                    "files": files_list,
                    "sessionIds": session_ids,
                    "strength": m_strength,
                    "version": (existing_match.get("version") or 1) + 1,
                    "parentId": existing_match["id"],
                    "supersedes": [existing_match["id"]] + (existing_match.get("supersedes") or []),
                    "sourceObservationIds": obs_ids,
                    "isLatest": True
                }
                if project:
                    evolved["project"] = project
                kv.set(KV.memories, evolved["id"], evolved)
                try:
                    _bm25_index.add(memory_to_observation(evolved))
                    if existing_match:
                        _bm25_index.remove(existing_match["id"])
                except Exception:
                    pass
                comb_text = evolved["title"] + " " + evolved["content"]
                vector_index_add_guarded(evolved["id"], "memory", comb_text, {"kind": "memory", "logId": evolved["id"]})
                if _vector_index and existing_match:
                    try:
                        _vector_index.remove(existing_match["id"])
                    except Exception:
                        pass
                consolidated_count += 1
            else:
                memory = {
                    "id": generate_id("mem"),
                    "createdAt": now,
                    "updatedAt": now,
                    "type": m_type,
                    "title": m_title,
                    "content": m_content,
                    "concepts": concepts_list,
                    "files": files_list,
                    "sessionIds": session_ids,
                    "strength": m_strength,
                    "version": 1,
                    "sourceObservationIds": obs_ids,
                    "isLatest": True
                }
                if project:
                    memory["project"] = project
                kv.set(KV.memories, memory["id"], memory)
                try:
                    _bm25_index.add(memory_to_observation(memory))
                except Exception:
                    pass
                comb_text = memory["title"] + " " + memory["content"]
                vector_index_add_guarded(memory["id"], "memory", comb_text, {"kind": "memory", "logId": memory["id"]})
                consolidated_count += 1
                
        except Exception as e:
            print(f"[consolidate] Concept '{concept}' failed: {e}")

    # === Semantic Memory Fact Merger ===
    summaries = kv.list(KV.summaries)
    new_facts_count = 0
    if len(summaries) >= 5:
        recent_summaries = sorted(
            summaries,
            key=lambda s: s.get("createdAt", ""),
            reverse=True
        )[:20]
        
        SEMANTIC_MERGE_SYSTEM = """You are a memory consolidation engine. Given overlapping episodic memories (session summaries), extract stable factual knowledge.
        
        Output format (XML):
        <facts>
          <fact confidence="0.0-1.0">Concise factual statement</fact>
        </facts>
        
        Rules:
        - Extract only facts that appear in 2+ episodes or are highly confident
        - Confidence reflects how well-supported the fact is across episodes
        - Combine overlapping information into single concise facts
        - Skip ephemeral details (specific error messages, temporary states)"""
        
        prompt_parts = []
        for i, s in enumerate(recent_summaries):
            prompt_parts.append(f"[Episode {i + 1}]\nTitle: {s.get('title')}\nNarrative: {s.get('narrative') or ''}\nConcepts: {', '.join(s.get('concepts') or [])}")
        merge_prompt = "Consolidate these episodic memories into stable facts:\n\n" + "\n\n".join(prompt_parts)
        
        try:
            response = generate_content(SEMANTIC_MERGE_SYSTEM, merge_prompt)
            fact_matches = re.findall(r'<fact\s+confidence="([^"]+)">([^<]+)</fact>', response, re.DOTALL)
            
            existing_semantic = kv.list(KV.semantic)
            now = datetime.datetime.utcnow().isoformat() + "Z"
            
            for conf_str, fact_text in fact_matches:
                fact_text = fact_text.strip()
                try:
                    confidence = float(conf_str)
                except Exception:
                    confidence = 0.5
                    
                existing = None
                for es in existing_semantic:
                    if es.get("fact", "").lower() == fact_text.lower():
                        existing = es
                        break
                        
                if existing:
                    existing["accessCount"] = (existing.get("accessCount") or 0) + 1
                    existing["lastAccessedAt"] = now
                    existing["updatedAt"] = now
                    existing["confidence"] = max(existing.get("confidence", 0.5), confidence)
                    kv.set(KV.semantic, existing["id"], existing)
                else:
                    sem = {
                        "id": generate_id("sem"),
                        "fact": fact_text,
                        "confidence": confidence,
                        "sourceSessionIds": [s["sessionId"] for s in recent_summaries if "sessionId" in s],
                        "sourceMemoryIds": [],
                        "accessCount": 1,
                        "lastAccessedAt": now,
                        "strength": confidence,
                        "createdAt": now,
                        "updatedAt": now
                    }
                    kv.set(KV.semantic, sem["id"], sem)
                    new_facts_count += 1
        except Exception as e:
            print(f"[consolidate] Semantic merge failed: {e}")

    # === Procedural Memory Extraction ===
    memories = kv.list(KV.memories)
    new_procs_count = 0
    patterns = []
    for m in memories:
        if m.get("isLatest") is not False and m.get("type") == "pattern":
            freq = len(m.get("sessionIds") or [])
            if freq >= 2:
                patterns.append({"content": m.get("content", ""), "frequency": freq})
                
    if len(patterns) >= 2:
        PROCEDURAL_EXTRACTION_SYSTEM = """You are a procedural memory extractor. Given repeated patterns and workflows observed across sessions, extract reusable procedures.
        
        Output format (XML):
        <procedures>
          <procedure name="short descriptive name" trigger="when to use this procedure">
            <step>Step 1 description</step>
            <step>Step 2 description</step>
          </procedure>
        </procedures>
        
        Rules:
        - Only extract procedures observed 2+ times
        - Steps should be concrete and actionable
        - Trigger condition should be specific enough to match automatically"""
        
        prompt_parts = []
        for i, p in enumerate(patterns):
            prompt_parts.append(f"[Pattern {i + 1}] (seen {p['frequency']}x)\n{p['content']}")
        proc_prompt = "Extract reusable procedures from these recurring patterns:\n\n" + "\n\n".join(prompt_parts)
        
        try:
            response = generate_content(PROCEDURAL_EXTRACTION_SYSTEM, proc_prompt)
            proc_matches = re.findall(r'<procedure\s+name="([^"]+)"\s+trigger="([^"]+)">([\s\S]*?)</procedure>', response, re.DOTALL)
            
            existing_procs = kv.list(KV.procedural)
            now = datetime.datetime.utcnow().isoformat() + "Z"
            
            for name, trigger, steps_block in proc_matches:
                steps = [s.strip() for s in re.findall(r'<step>([^<]+)</step>', steps_block, re.DOTALL)]
                
                existing = None
                for ep in existing_procs:
                    if ep.get("name", "").lower() == name.lower():
                        existing = ep
                        break
                        
                if existing:
                    existing["frequency"] = (existing.get("frequency") or 1) + 1
                    existing["updatedAt"] = now
                    existing["strength"] = min(1.0, (existing.get("strength") or 0.5) + 0.1)
                    kv.set(KV.procedural, existing["id"], existing)
                else:
                    proc = {
                        "id": generate_id("proc"),
                        "name": name,
                        "steps": steps,
                        "triggerCondition": trigger,
                        "frequency": 1,
                        "sourceSessionIds": [],
                        "strength": 0.5,
                        "createdAt": now,
                        "updatedAt": now
                    }
                    kv.set(KV.procedural, proc["id"], proc)
                    new_procs_count += 1
        except Exception as e:
            print(f"[consolidate] Procedural extraction failed: {e}")

    res_summary = {
        "success": True,
        "consolidated": consolidated_count,
        "totalObservations": len(all_obs),
        "semantic": {
            "newFacts": new_facts_count,
            "totalSummaries": len(summaries)
        },
        "procedural": {
            "newProcedures": new_procs_count,
            "patternsAnalyzed": len(patterns)
        }
    }
    if _index_persistence and consolidated_count > 0:
        _index_persistence.schedule_save()
    safe_audit(kv, "consolidate", "mem::consolidate-pipeline", [], res_summary)
    commit_if_enabled(kv, f"Consolidation complete: consolidated={consolidated_count}, facts={new_facts_count}, procs={new_procs_count}", "system")
    return res_summary

# =====================================================================
# Folder Graph
# =====================================================================

def folder_color(path: str) -> str:
    """Hash a folder path string to an HSL color string.

    Replicates the JS ``folderColor(id)`` function in src/viewer/index.html
    exactly, using the light-mode lightness range (38 + h%14).

    Algorithm (matches JS):
        h = 0
        for each char: h = (h * 31 + ord(char)) & 0xfffffff
        hue = (h % 360 + 360) % 360
        sat = 55 + (h % 25)          # percent, 55-79
        lig = 38 + (h % 14)          # percent, 38-51 (light mode)

    Returns:
        A CSS ``hsl(hue, sat%, lig%)`` string.
    """
    h = 0
    for ch in path:
        h = (h * 31 + ord(ch)) & 0xFFFFFFF

    hue = (h % 360 + 360) % 360
    sat_pct = 55 + (h % 25)
    lig_pct = 38 + (h % 14)

    return f"hsl({hue}, {sat_pct}%, {lig_pct}%)"


def folder_graph_build(kv: StateKV) -> Dict[str, Any]:
    """Build graph data for the viewer's Graph tab.

    Reads all (folder_path, agent_id) pairs from ``KV.folders``,
    aggregates per-folder node metadata, loads observations to collect
    text for cross-reference edge detection, then emits three edge types:

    - ``same-parent``: two folders share the same ``os.path.dirname``
    - ``cross-ref``:   folder A's combined obs text contains folder B's path
    - ``agent-shared``: two folders share a common agentId

    Returns:
        {"nodes": [...], "edges": [...]}

    Each node::

        {
            "id": folderPath,
            "label": basename(folderPath),
            "folderPath": folderPath,
            "agentIds": [...],
            "obsCount": int,
            "color": "#rrggbb",
        }

    Each edge::

        {
            "source": folderPath,
            "target": folderPath,
            "type": "same-parent" | "cross-ref" | "agent-shared",
            # agent-shared only:
            "agentId": str,
        }

    Edges are deduplicated on (source, target, type).
    """
    index_entries = kv.list(KV.folders)
    if is_agent_scope_isolated():
        aid = get_agent_id()
        if aid:
            index_entries = [e for e in index_entries if e.get("agentId") == aid]

    # --- Build folder_map and collect obs text per (folder, agent) pair ---
    # folder_map: folderPath -> {"folderPath", "agentIds": set, "obsCount", "color"}
    folder_map: Dict[str, Dict[str, Any]] = {}
    # pair_obs_texts: (folder_path, agent_id) -> combined text string
    pair_obs_texts: Dict[Tuple[str, str], str] = {}

    for entry in index_entries:
        fp = entry.get("folderPath", "")
        aid = entry.get("agentId", "")
        if not fp:
            continue

        if fp not in folder_map:
            folder_map[fp] = {
                "folderPath": fp,
                "agentIds": set(),
                "obsCount": 0,
                "color": folder_color(fp),
            }

        folder_map[fp]["agentIds"].add(aid)
        folder_map[fp]["obsCount"] += entry.get("obsCount", 0)

        # Load observations to build combined text for cross-ref detection
        obs_scope = KV.folder_obs(fp, aid)
        obs_list = kv.list(obs_scope)
        combined_parts = []
        for obs in obs_list:
            text = obs.get("text") or ""
            title = obs.get("title") or ""
            combined_parts.append(f"{text} {title}")
        pair_obs_texts[(fp, aid)] = " ".join(combined_parts)

    # --- Build nodes ---
    nodes = []
    for fp, info in folder_map.items():
        nodes.append({
            "id": fp,
            "label": os.path.basename(fp) or fp,
            "folderPath": fp,
            "agentIds": sorted(info["agentIds"]),
            "obsCount": info["obsCount"],
            "color": info["color"],
        })

    # --- Build edges ---
    edges: List[Dict[str, Any]] = []
    # Deduplicate on (frozenset(source, target), type) so that (A,B) and (B,A)
    # are treated as the same edge (REQ-028).
    seen_edges: Set[Tuple[Any, str]] = set()

    def add_edge(edge: Dict[str, Any]) -> None:
        key = (frozenset([edge["source"], edge["target"]]), edge["type"])
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append(edge)

    folder_paths = list(folder_map.keys())

    # Edge type 1 — same-parent
    for i in range(len(folder_paths)):
        for j in range(i + 1, len(folder_paths)):
            a = folder_paths[i]
            b = folder_paths[j]
            # Use posixpath-style dirname on forward-slash paths
            if a.rsplit("/", 1)[0] == b.rsplit("/", 1)[0] and "/" in a and "/" in b:
                add_edge({"source": a, "target": b, "type": "same-parent"})
            elif os.path.dirname(a) == os.path.dirname(b) and os.path.dirname(a) != "":
                add_edge({"source": a, "target": b, "type": "same-parent"})

    # Edge type 2 — cross-reference (folder A's obs text mentions folder B's path)
    for (fp_a, _agent_a), text_a in pair_obs_texts.items():
        for fp_b in folder_paths:
            if fp_b != fp_a and fp_b in text_a:
                add_edge({"source": fp_a, "target": fp_b, "type": "cross-ref"})

    # Edge type 3 — agent-shared (two folders share the same agentId)
    # Build: agentId -> [folder_paths that have this agent]
    agent_to_folders: Dict[str, List[str]] = {}
    for fp, info in folder_map.items():
        for aid in info["agentIds"]:
            agent_to_folders.setdefault(aid, []).append(fp)

    for aid, fps in agent_to_folders.items():
        for i in range(len(fps)):
            for j in range(i + 1, len(fps)):
                add_edge({
                    "source": fps[i],
                    "target": fps[j],
                    "type": "agent-shared",
                    "agentId": aid,
                })

    return {"nodes": nodes, "edges": edges}


# Setup persistence helper wire-ups
def set_index_persistence(persistence: IndexPersistence) -> None:
    global _index_persistence
    _index_persistence = persistence

def set_embedding_provider(provider) -> None:
    global _embedding_provider, _hybrid_search
    _embedding_provider = provider
    _hybrid_search = HybridSearch(
        _bm25_index,
        _vector_index,
        _embedding_provider,
        None
    )

def set_stream_broadcaster(broadcaster) -> None:
    global _stream_broadcaster
    _stream_broadcaster = broadcaster

def broadcast_stream(payload: Dict[str, Any]) -> None:
    if _stream_broadcaster:
        try:
            _stream_broadcaster(payload)
        except Exception as e:
            print(f"[broadcaster] Failed: {e}")


def backfill_obs_lookup_if_needed(kv: StateKV) -> None:
    """Ensure every folder observation has an entry in KV.obs_lookup."""
    folders = kv.list(KV.folders)
    if not folders:
        return

    # Check if lookup index needs populating
    lookups = kv.list(KV.obs_lookup)
    if len(lookups) >= sum(int(f.get("obsCount", 0)) for f in folders):
        return  # already populated

    print("[db] Backfilling obs_lookup index...")
    for entry in folders:
        fp = entry.get("folderPath")
        aid = entry.get("agentId")
        if not fp or not aid:
            continue
        obs_list = kv.list(KV.folder_obs(fp, aid))
        for obs in obs_list:
            oid = obs.get("id")
            if oid:
                kv.set(KV.obs_lookup, oid, {"folderPath": fp, "agentId": aid})
    print("[db] obs_lookup backfill complete.")


def verify_index_sync_on_boot(kv: StateKV) -> bool:
    """Check if the search index size matches the database counts.
    Returns True if in sync, False if a rebuild is needed.
    """
    try:
        # 1. Total folder obs count
        folders = kv.list(KV.folders)
        folder_obs_count = sum(int(f.get("obsCount", 0)) for f in folders)

        # 2. Total memories count
        memories = kv.list(KV.memories)
        latest_memories_count = len([m for m in memories if m.get("isLatest") is not False])

        # 3. Total legacy observations count
        legacy_obs_count = 0
        try:
            sessions = kv.list(KV.sessions)
            for s in sessions:
                sid = s.get("id")
                if sid:
                    obs_list = kv.list(KV.observations(sid))
                    # Only legacy observations that were indexed (having title and narrative)
                    legacy_obs_count += len([o for o in obs_list if o.get("title") and o.get("narrative")])
        except Exception:
            pass

        total_db_count = folder_obs_count + latest_memories_count + legacy_obs_count
        index_size = _bm25_index.size

        if total_db_count != index_size:
            print(f"[persistence] Index out of sync with DB (DB={total_db_count}, Index={index_size}). Rebuild required.")
            return False

        print(f"[persistence] Index is in sync with DB (size={index_size}).")
        return True
    except Exception as e:
        print(f"[persistence] verify_index_sync_on_boot failed: {e}")
        return False

