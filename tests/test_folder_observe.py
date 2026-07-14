"""Unit tests for folder_observe (REQ-008, REQ-010, REQ-011, REQ-015)."""

import sys
import os
import pytest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from db import StateKV
from functions import folder_observe, KV


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    return StateKV(db_path=db_path)


def base_payload(**overrides):
    payload = {
        "folderPath": "/home/user/projects/myapp",
        "agentId": "kiro",
        "text": "Edited src/app.py to add a new route",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    payload.update(overrides)
    return payload


class TestFolderObserveMissingFields:
    def test_missing_folder_path(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError, match="folderPath"):
            folder_observe(kv, base_payload(folderPath=""))

    def test_missing_agent_id(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError, match="agentId"):
            folder_observe(kv, base_payload(agentId=""))

    def test_missing_text(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError, match="text"):
            folder_observe(kv, base_payload(text=""))

    def test_missing_timestamp_defaults(self, tmp_path):
        kv = make_kv(tmp_path)
        payload = base_payload()
        del payload["timestamp"]
        # timestamp is required — should raise
        with pytest.raises(ValueError, match="timestamp"):
            folder_observe(kv, payload)


class TestFolderObserveSuccess:
    def test_returns_observation_id(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_observe(kv, base_payload())
        assert "observationId" in result
        assert result["observationId"].startswith("fobs_")

    def test_obs_stored_in_kv(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_observe(kv, base_payload())
        obs_id = result["observationId"]
        fp = "home/user/projects/myapp"  # normalized
        stored = kv.get(KV.folder_obs(fp, "kiro"), obs_id)
        assert stored is not None
        assert stored["id"] == obs_id

    def test_obs_count_incremented(self, tmp_path):
        kv = make_kv(tmp_path)
        folder_observe(kv, base_payload(text="First observation"))
        folder_observe(kv, base_payload(text="Second observation"))
        fp = "home/user/projects/myapp"
        meta = kv.get(KV.folder_meta(fp, "kiro"), "meta")
        assert meta is not None
        assert meta["obsCount"] == 2

    def test_folders_index_upserted(self, tmp_path):
        kv = make_kv(tmp_path)
        folder_observe(kv, base_payload())
        fp = "home/user/projects/myapp"
        entry = kv.get(KV.folders, f"{fp}:kiro")
        assert entry is not None
        assert entry["folderPath"] == fp
        assert entry["agentId"] == "kiro"

    def test_text_capped_at_4000(self, tmp_path):
        kv = make_kv(tmp_path)
        long_text = "x" * 5000
        result = folder_observe(kv, base_payload(text=long_text))
        fp = "home/user/projects/myapp"
        stored = kv.get(KV.folder_obs(fp, "kiro"), result["observationId"])
        assert len(stored["text"]) <= 4000


class TestFolderObserveCap:
    def test_cap_enforced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_OBS_PER_FOLDER", "3")
        kv = make_kv(tmp_path)
        for i in range(3):
            folder_observe(kv, base_payload(text=f"observation {i}"))
        with pytest.raises(ValueError, match="limit"):
            folder_observe(kv, base_payload(text="observation 4"))


class TestFolderObservePairIsolation:
    def test_different_pairs_isolated(self, tmp_path):
        kv = make_kv(tmp_path)
        folder_observe(kv, base_payload(folderPath="/home/user/proj-a", agentId="kiro"))
        folder_observe(
            kv, base_payload(folderPath="/home/user/proj-b", agentId="claude")
        )
        fp_a = "home/user/proj-a"
        fp_b = "home/user/proj-b"
        obs_a = kv.list(KV.folder_obs(fp_a, "kiro"))
        obs_b = kv.list(KV.folder_obs(fp_b, "claude"))
        ids_a = {o["id"] for o in obs_a}
        ids_b = {o["id"] for o in obs_b}
        assert ids_a.isdisjoint(ids_b)
