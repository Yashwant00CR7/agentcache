import os
import json
import sqlite3
import threading
import time
import atexit
from typing import Dict, Any, List, Optional, TypeVar

T = TypeVar("T")

DB_PATH = os.path.join(os.path.expanduser("~"), ".agentcache", "agentcache.db")


class StateKV:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        # Per-thread persistent connection pool (A3.1)
        self._local = threading.local()
        self._init_db()
        # Register WAL checkpoint on graceful shutdown (A3.2)
        atexit.register(self._wal_checkpoint)

    def _get_conn(self) -> sqlite3.Connection:
        """Return a per-thread persistent connection, creating one if needed (A3.1)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _wal_checkpoint(self) -> None:
        """Flush WAL frames to the main database file on shutdown (A3.2)."""
        try:
            conn = getattr(self._local, "conn", None)
            if conn:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
                conn.commit()
            else:
                # Open a temporary connection just for the checkpoint
                tmp = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
                try:
                    tmp.execute("PRAGMA wal_checkpoint(FULL)")
                    tmp.commit()
                finally:
                    tmp.close()
            print("[db] WAL checkpoint completed on shutdown.")
        except Exception as e:
            print(f"[db] WAL checkpoint failed: {e}")

    def teardown(self) -> None:
        """Close the per-thread connection and flush WAL (for explicit cleanup)."""
        self._wal_checkpoint()
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def stats(self) -> Dict[str, Any]:
        """Return DB statistics for the /health endpoint (A3.3).

        Returns:
            {
                "db_size_bytes": int,
                "kv_row_count": int,
                "audit_row_count": int,
                "wal_size_bytes": int,
            }
        """
        result: Dict[str, Any] = {
            "db_size_bytes": 0,
            "kv_row_count": 0,
            "audit_row_count": 0,
            "wal_size_bytes": 0,
        }
        try:
            if os.path.exists(self.db_path):
                result["db_size_bytes"] = os.path.getsize(self.db_path)
            wal_path = self.db_path + "-wal"
            if os.path.exists(wal_path):
                result["wal_size_bytes"] = os.path.getsize(wal_path)
        except Exception:
            pass
        try:
            conn = self._get_conn()
            result["kv_row_count"] = conn.execute(
                "SELECT COUNT(*) FROM kv_store"
            ).fetchone()[0]
            result["audit_row_count"] = conn.execute(
                "SELECT COUNT(*) FROM audit_log"
            ).fetchone()[0]
        except Exception:
            pass
        return result

    def _init_db(self):
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            # Use a temporary direct connection for initialization (before _local is set)
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS kv_store (
                        scope TEXT NOT NULL,
                        key   TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (scope, key)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kv_scope ON kv_store(scope)"
                )
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts        INTEGER NOT NULL,
                        agent_id  TEXT NOT NULL,
                        message   TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state_metadata (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                conn.commit()
            finally:
                conn.close()
            print(f"[db] SQLite database initialized at {self.db_path}")
        except Exception as e:
            print(f"[db] WARNING initializing SQLite database: {e}")

    def get(self, scope: str, key: str) -> Optional[Any]:
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT value FROM kv_store WHERE scope = ? AND key = ?", (scope, key)
            ).fetchone()
            if row:
                val = json.loads(row["value"])
                if isinstance(val, dict) and "id" not in val:
                    val["id"] = key
                return val
            return None
        except Exception as e:
            print(f"[db] get failed (scope={scope}, key={key}): {e}")
            return None

    def set(self, scope: str, key: str, value: Any) -> Any:
        with self._lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (scope, key, value) VALUES (?, ?, ?)",
                    (scope, key, json.dumps(value)),
                )
                conn.commit()
                return value
            except Exception as e:
                print(f"[db] set failed (scope={scope}, key={key}): {e}")
                return value

    def delete(self, scope: str, key: str) -> bool:
        with self._lock:
            try:
                conn = self._get_conn()
                cur = conn.execute(
                    "DELETE FROM kv_store WHERE scope = ? AND key = ?", (scope, key)
                )
                conn.commit()
                return cur.rowcount > 0
            except Exception as e:
                print(f"[db] delete failed (scope={scope}, key={key}): {e}")
                return False

    def list(self, scope: str) -> List[Any]:
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT key, value FROM kv_store WHERE scope = ?", (scope,)
            ).fetchall()
            results = []
            for r in rows:
                val = json.loads(r["value"])
                if isinstance(val, dict) and "id" not in val:
                    val["id"] = r["key"]
                results.append(val)
            return results
        except Exception as e:
            print(f"[db] list failed (scope={scope}): {e}")
            return []

    def update(self, scope: str, key: str, ops: List[Dict[str, Any]]) -> Optional[Any]:
        with self._lock:
            try:
                conn = self._get_conn()
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE scope = ? AND key = ?",
                    (scope, key),
                ).fetchone()
                obj = json.loads(row["value"]) if row else {}
                if not isinstance(obj, dict):
                    obj = {}
                if "id" not in obj:
                    obj["id"] = key

                for op in ops:
                    op_type = op.get("type")
                    path = op.get("path")
                    val = op.get("value")
                    if not path:
                        continue
                    if op_type == "set":
                        if "." in path:
                            parts = path.split(".")
                            curr = obj
                            for part in parts[:-1]:
                                if part not in curr or not isinstance(curr[part], dict):
                                    curr[part] = {}
                                curr = curr[part]
                            curr[parts[-1]] = val
                        else:
                            obj[path] = val
                    elif op_type == "delete":
                        if "." in path:
                            parts = path.split(".")
                            curr = obj
                            for part in parts[:-1]:
                                if part not in curr or not isinstance(curr[part], dict):
                                    break
                                curr = curr[part]
                            else:
                                curr.pop(parts[-1], None)
                        else:
                            obj.pop(path, None)

                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (scope, key, value) VALUES (?, ?, ?)",
                    (scope, key, json.dumps(obj)),
                )
                conn.commit()
                return obj
            except Exception as e:
                print(f"[db] update failed (scope={scope}, key={key}): {e}")
                return None

    def commit_version(self, message: str, agent_id: str) -> Optional[str]:
        """Write an audit log entry instead of a Dolt commit."""
        author = agent_id or "unknown-agent"
        with self._lock:
            try:
                conn = self._get_conn()
                cur = conn.execute(
                    "INSERT INTO audit_log (ts, agent_id, message) VALUES (?, ?, ?)",
                    (int(time.time() * 1000), author, message),
                )
                conn.commit()
                row_id = str(cur.lastrowid)
                print(f"[audit] {author}: {message} (id={row_id})")
                return row_id
            except Exception as e:
                print(f"[audit] commit_version failed: {e}")
                return None

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, ts, agent_id, message FROM audit_log ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[db] get_audit_log failed: {e}")
            return []
