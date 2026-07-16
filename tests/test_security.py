"""
tests/test_security.py

Advanced security and injection checks for the agentcache-python project (A2.3, A3.1).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from agentcache.db import StateKV
from agentcache.storage.paths import normalize_folder_path


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "security_test.db"
    return StateKV(db_path=str(db_file))


def test_sql_injection_get_set_delete(temp_db):
    """Verify that parameterized SQL queries prevent SQL injection payloads from hijacking queries."""
    # List of classic SQL injection strings
    payloads = [
        "' OR '1'='1",
        "'; DROP TABLE kv_store; --",
        "UNION SELECT 'a', 'b', 'c'",
        '" OR ""="',
        "'; INSERT INTO kv_store VALUES ('abc', 'xyz', '123'); --",
        "'; DELETE FROM kv_store; --",
    ]

    # Store a benign test key to monitor database state integrity
    temp_db.set("mem:folders", "benign_key", {"name": "test_folder"})

    for payload in payloads:
        # 1. Check get operations
        # Malicious key/scope should simply return None (no SQL exception, no data leaks)
        res = temp_db.get("mem:folders", payload)
        assert res is None

        # 2. Check set operations
        # Malicious key/scope should be safely stored as a literal string value without executing SQL commands
        temp_db.set(payload, "malicious_key", {"data": payload})
        retrieved = temp_db.get(payload, "malicious_key")
        assert retrieved == {"data": payload, "id": "malicious_key"}

        # 3. Check delete operations
        # Malicious deletion should not delete the benign data
        temp_db.delete("mem:folders", payload)

        # Verify database state integrity remains untouched
        benign_data = temp_db.get("mem:folders", "benign_key")
        assert benign_data == {"name": "test_folder", "id": "benign_key"}


def test_path_traversal_payloads():
    """Verify that normalize_folder_path rejects all variations of directory traversal payloads."""
    dangerous_paths = [
        "../../etc/passwd",
        "projects/../../etc/shadow",
        "C:\\..\\Windows\\System32\\cmd.exe",
        "projects/myapp/../../..",
        "../",
        "..",
        "/../",
        "\\\\server\\share\\..\\file",
        "a/b/c/../../../../d",
    ]

    for path in dangerous_paths:
        with pytest.raises(ValueError, match="path traversal segment '..'"):
            normalize_folder_path(path)


def test_empty_and_invalid_slash_rejections():
    """Verify that empty inputs and paths consisting only of slashes raise ValueError."""
    invalid_paths = ["", "///", "////"]
    for path in invalid_paths:
        with pytest.raises(ValueError):
            normalize_folder_path(path)
