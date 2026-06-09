import os
import json
import sqlite3
import threading
import time
from typing import Dict, Any, List, Optional, TypeVar, Union

T = TypeVar("T")

DB_PATH = os.path.join(os.path.expanduser("~"), ".agentmemory", "agentmemory.db")

class StateKV:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS kv_store (
                        scope TEXT NOT NULL,
                        key   TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (scope, key)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kv_scope ON kv_store(scope)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts        INTEGER NOT NULL,
                        agent_id  TEXT NOT NULL,
                        message   TEXT NOT NULL
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
            try:
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE scope = ? AND key = ?",
                    (scope, key)
                ).fetchone()
                if row:
                    val = json.loads(row["value"])
                    if isinstance(val, dict) and "id" not in val:
                        val["id"] = key
                    return val
                return None
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] get failed (scope={scope}, key={key}): {e}")
            return None

    def set(self, scope: str, key: str, value: Any) -> Any:
        try:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (scope, key, value) VALUES (?, ?, ?)",
                    (scope, key, json.dumps(value))
                )
                conn.commit()
                return value
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] set failed (scope={scope}, key={key}): {e}")
            return value

    def delete(self, scope: str, key: str) -> bool:
        try:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "DELETE FROM kv_store WHERE scope = ? AND key = ?",
                    (scope, key)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] delete failed (scope={scope}, key={key}): {e}")
            return False

    def list(self, scope: str) -> List[Any]:
        try:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT key, value FROM kv_store WHERE scope = ?",
                    (scope,)
                ).fetchall()
                results = []
                for r in rows:
                    val = json.loads(r["value"])
                    if isinstance(val, dict) and "id" not in val:
                        val["id"] = r["key"]
                    results.append(val)
                return results
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] list failed (scope={scope}): {e}")
            return []

    def update(self, scope: str, key: str, ops: List[Dict[str, Any]]) -> Optional[Any]:
        with self._lock:
            try:
                conn = self._get_conn()
                try:
                    row = conn.execute(
                        "SELECT value FROM kv_store WHERE scope = ? AND key = ?",
                        (scope, key)
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
                        (scope, key, json.dumps(obj))
                    )
                    conn.commit()
                    return obj
                finally:
                    conn.close()
            except Exception as e:
                print(f"[db] update failed (scope={scope}, key={key}): {e}")
                return None

    def commit_version(self, message: str, agent_id: str) -> Optional[str]:
        """Write an audit log entry instead of a Dolt commit."""
        author = agent_id or "unknown-agent"
        try:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO audit_log (ts, agent_id, message) VALUES (?, ?, ?)",
                    (int(time.time() * 1000), author, message)
                )
                conn.commit()
                row_id = str(cur.lastrowid)
                print(f"[audit] {author}: {message} (id={row_id})")
                return row_id
            finally:
                conn.close()
        except Exception as e:
            print(f"[audit] commit_version failed: {e}")
            return None

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, ts, agent_id, message FROM audit_log ORDER BY ts DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] get_audit_log failed: {e}")
            return []
