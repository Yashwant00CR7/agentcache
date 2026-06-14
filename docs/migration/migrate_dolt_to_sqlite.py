#!/usr/bin/env python3
"""
One-time migration: export all kv_store rows from local Dolt → agentmemory.db (SQLite).
Run ONCE while local Dolt SQL server is running on 127.0.0.1:3306.
"""
import os, sys, json, sqlite3, time

env_path = os.path.expanduser("~/.agentmemory/.env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

try:
    import pymysql
except ImportError:
    print("[migrate] pymysql not installed. Run: pip install pymysql")
    sys.exit(1)

DOLT_HOST = os.environ.get("DOLT_HOST", "127.0.0.1")
DOLT_PORT = int(os.environ.get("DOLT_PORT", "3306"))
DOLT_USER = os.environ.get("DOLT_USER", "root")
DOLT_PASS = os.environ.get("DOLT_PASSWORD", "")
DOLT_DB   = os.environ.get("DOLT_DATABASE", "agentmemory")
SQLITE_PATH = os.path.expanduser("~/.agentmemory/agentmemory.db")

print(f"[migrate] Connecting to Dolt at {DOLT_HOST}:{DOLT_PORT}/{DOLT_DB}...")
try:
    dolt = pymysql.connect(
        host=DOLT_HOST, port=DOLT_PORT, user=DOLT_USER,
        password=DOLT_PASS, database=DOLT_DB,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True
    )
except Exception as e:
    print(f"[migrate] Dolt connection failed: {e}")
    print("[migrate] Make sure Dolt SQL server is running: dolt sql-server --host 127.0.0.1 --port 3306 --data-dir ~/.agentmemory/dolt")
    sys.exit(1)

print(f"[migrate] Opening SQLite at {SQLITE_PATH}...")
db = sqlite3.connect(SQLITE_PATH)
db.execute("PRAGMA journal_mode=WAL")
db.execute("""
    CREATE TABLE IF NOT EXISTS kv_store (
        scope TEXT NOT NULL,
        key   TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (scope, key)
    )
""")
db.execute("CREATE INDEX IF NOT EXISTS idx_kv_scope ON kv_store(scope)")
db.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       INTEGER NOT NULL,
        agent_id TEXT NOT NULL,
        message  TEXT NOT NULL
    )
""")
db.commit()

# Read all rows from Dolt
with dolt.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM kv_store")
    total = cur.fetchone()["n"]
    print(f"[migrate] Found {total} rows in Dolt kv_store")

    cur.execute("SELECT scope, `key`, value FROM kv_store")
    rows = cur.fetchall()

inserted = 0
for row in rows:
    try:
        db.execute(
            "INSERT OR REPLACE INTO kv_store (scope, key, value) VALUES (?, ?, ?)",
            (row["scope"], row["key"], row["value"])
        )
        inserted += 1
    except Exception as e:
        print(f"[migrate] row error ({row['scope']}/{row['key']}): {e}")

db.commit()
print(f"[migrate] Inserted {inserted}/{total} rows into SQLite")

# Try to migrate dolt_log as audit entries
try:
    with dolt.cursor() as cur:
        cur.execute("SELECT committer, date, message FROM dolt_log ORDER BY date ASC LIMIT 1000")
        logs = cur.fetchall()
    for l in logs:
        ts = int(l["date"].timestamp() * 1000) if hasattr(l["date"], "timestamp") else int(time.time() * 1000)
        db.execute(
            "INSERT INTO audit_log (ts, agent_id, message) VALUES (?, ?, ?)",
            (ts, l["committer"] or "unknown", l["message"] or "")
        )
    db.commit()
    print(f"[migrate] Migrated {len(logs)} dolt_log entries to audit_log")
except Exception as e:
    print(f"[migrate] dolt_log migration skipped: {e}")

dolt.close()
db.close()

size_mb = os.path.getsize(SQLITE_PATH) / 1024 / 1024
print(f"[migrate] Done! SQLite file: {SQLITE_PATH} ({size_mb:.1f} MB)")
print("[migrate] Now run: python push_local_data.py  (or python -c below)")
print("""
  python -c "
import os, sys
env = os.path.expanduser('~/.agentmemory/.env')
with open(env) as f:
    for l in f:
        l=l.strip()
        if l and not l.startswith('#') and '=' in l:
            k,v=l.split('=',1); os.environ[k.strip()]=v.strip().strip('\\\"').strip(\\\"'\\\")
from huggingface_hub import HfApi
api = HfApi(token=os.environ['HF_TOKEN'])
api.upload_file(
    path_or_fileobj=os.path.expanduser('~/.agentmemory/agentmemory.db'),
    path_in_repo='agentmemory.db',
    repo_id=os.environ.get('AGENTMEMORY_DATASET_REPO','Yash030/agentmemory-python-data'),
    repo_type='dataset', token=os.environ['HF_TOKEN'],
    commit_message='restore: upload migrated SQLite DB'
)
print('Uploaded!')
"
""")
