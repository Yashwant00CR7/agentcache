import os
import json
import pymysql
import urllib.parse
from typing import Dict, Any, List, Optional, TypeVar, Union

T = TypeVar("T")

class StateKV:
    def __init__(self):
        self.host = os.getenv("DOLT_HOST", "127.0.0.1")
        self.port = int(os.getenv("DOLT_PORT", "3306"))
        self.user = os.getenv("DOLT_USER", "root")
        self.password = os.getenv("DOLT_PASSWORD", "")
        self.database = os.getenv("DOLT_DATABASE", "agentmemory")
        
        self._init_db()

    def _get_conn(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )

    def _init_db(self):
        try:
            # First, check connection and create database if not exists
            conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                charset="utf8mb4",
                autocommit=True
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.database}")
            finally:
                conn.close()
            
            # Now connect to database and initialize schema
            conn = self._get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS kv_store (
                            scope VARCHAR(255) NOT NULL,
                            `key` VARCHAR(255) NOT NULL,
                            value LONGTEXT NOT NULL,
                            PRIMARY KEY (scope, `key`)
                        )
                    """)
                    try:
                        cursor.execute("CREATE INDEX idx_kv_scope ON kv_store(scope)")
                    except Exception as e:
                        # Index might already exist, ignore error 1061 / 1105 (duplicate key name)
                        if hasattr(e, 'args') and len(e.args) > 0 and (e.args[0] in (1061, 1105) or "Duplicate key name" in str(e)):
                            pass
                        else:
                            raise e
            finally:
                conn.close()
            print(f"[db] Dolt database '{self.database}' initialized successfully.")
        except Exception as e:
            print(f"[db] WARNING initializing Dolt database: {e}")
            print(f"[db] Please ensure your Dolt SQL Server is running on {self.host}:{self.port}")
            # Do not raise error to allow the app to boot, though subsequent operations will fail if Dolt is off.

    def get(self, scope: str, key: str) -> Optional[Any]:
        try:
            conn = self._get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT value FROM kv_store WHERE scope = %s AND `key` = %s",
                        (scope, key)
                    )
                    row = cursor.fetchone()
                    if row:
                        return json.loads(row["value"])
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
                serialized = json.dumps(value)
                with conn.cursor() as cursor:
                    cursor.execute(
                        "REPLACE INTO kv_store (scope, `key`, value) VALUES (%s, %s, %s)",
                        (scope, key, serialized)
                    )
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
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM kv_store WHERE scope = %s AND `key` = %s",
                        (scope, key)
                    )
                    affected = cursor.rowcount
                return affected > 0
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] delete failed (scope={scope}, key={key}): {e}")
            return False

    def list(self, scope: str) -> List[Any]:
        try:
            conn = self._get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT value FROM kv_store WHERE scope = %s",
                        (scope,)
                    )
                    rows = cursor.fetchall()
                    return [json.loads(row["value"]) for row in rows]
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] list failed (scope={scope}): {e}")
            return []

    def update(self, scope: str, key: str, ops: List[Dict[str, Any]]) -> Optional[Any]:
        try:
            conn = self._get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT value FROM kv_store WHERE scope = %s AND `key` = %s FOR UPDATE",
                        (scope, key)
                    )
                    row = cursor.fetchone()
                    obj = json.loads(row["value"]) if row else {}
                    if not isinstance(obj, dict):
                        obj = {}

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

                    serialized = json.dumps(obj)
                    cursor.execute(
                        "REPLACE INTO kv_store (scope, `key`, value) VALUES (%s, %s, %s)",
                        (scope, key, serialized)
                    )
                    return obj
            finally:
                conn.close()
        except Exception as e:
            print(f"[db] update failed (scope={scope}, key={key}): {e}")
            return None

    def commit_version(self, message: str, agent_id: str) -> Optional[str]:
        author_name = agent_id or "unknown-agent"
        author_email = f"{author_name}@agentmemory.ai"
        author_str = f"{author_name} <{author_email}>"
        
        try:
            conn = self._get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("CALL dolt_add('-A')")
                    cursor.execute("CALL dolt_commit('-m', %s, '--author', %s)", (message, author_str))
                    row = cursor.fetchone()
                    if row:
                        val = list(row.values())[0] if isinstance(row, dict) else row[0]
                        print(f"[dolt commit] Created commit: {val}")
                        return val
                    return None
            finally:
                conn.close()
        except Exception as e:
            print(f"[dolt commit] Failed: {e}")
            return None
