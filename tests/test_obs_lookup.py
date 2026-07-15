"""Unit tests for observation lookup index, backfill, and index sync validation."""

import datetime
import os

from agentcache import functions
from agentcache.db import StateKV
from agentcache.functions import (
    KV,
    backfill_obs_lookup_if_needed,
    folder_observe,
    forget,
    verify_index_sync_on_boot,
)


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), "test_obs_lookup.db")
    return StateKV(db_path=db_path)


def base_payload(folder="/home/user/proj", agent="kiro", text="Test observation"):
    return {
        "folderPath": folder,
        "agentId": agent,
        "text": text,
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


class TestObsLookupFlows:
    def test_lookup_added_on_observe(self, tmp_path):
        kv = make_kv(tmp_path)
        res = folder_observe(kv, base_payload())
        obs_id = res["observationId"]

        # Verify entry in KV.obs_lookup
        lookup = kv.get(KV.obs_lookup, obs_id)
        assert lookup is not None
        assert lookup["folderPath"] == "home/user/proj"
        assert lookup["agentId"] == "kiro"

    def test_lookup_deleted_on_forget(self, tmp_path):
        kv = make_kv(tmp_path)
        res = folder_observe(kv, base_payload())
        obs_id = res["observationId"]

        # Confirm it exists
        assert kv.get(KV.obs_lookup, obs_id) is not None

        # Delete it using forget
        forget(
            kv,
            {
                "folderPath": "/home/user/proj",
                "agentId": "kiro",
                "observationIds": [obs_id],
            },
        )

        # Confirm it is gone from both stores
        assert kv.get(KV.obs_lookup, obs_id) is None
        assert kv.get(KV.folder_obs("home/user/proj", "kiro"), obs_id) is None

    def test_backfill_populates_missing_lookups(self, tmp_path):
        kv = make_kv(tmp_path)

        # Ingest observations
        res1 = folder_observe(kv, base_payload(text="First"))
        res2 = folder_observe(kv, base_payload(text="Second"))
        obs1 = res1["observationId"]
        obs2 = res2["observationId"]

        # Manually clear the lookup index (simulating legacy data)
        kv.delete(KV.obs_lookup, obs1)
        kv.delete(KV.obs_lookup, obs2)
        assert kv.get(KV.obs_lookup, obs1) is None
        assert kv.get(KV.obs_lookup, obs2) is None

        # Run backfill
        backfill_obs_lookup_if_needed(kv)

        # Verify populated
        lookup1 = kv.get(KV.obs_lookup, obs1)
        lookup2 = kv.get(KV.obs_lookup, obs2)
        assert lookup1 is not None
        assert lookup2 is not None
        assert lookup1["folderPath"] == "home/user/proj"
        assert lookup2["folderPath"] == "home/user/proj"

    def test_verify_index_sync_detects_mismatch(self, tmp_path):
        kv = make_kv(tmp_path)

        # Clear indexes
        functions._bm25_index.clear()
        if functions._vector_index:
            functions._vector_index.clear()

        # Ingest one observation
        res = folder_observe(kv, base_payload())
        obs_id = res["observationId"]

        # BM25 size should be 1
        assert functions._bm25_index.size == 1

        # verify_index_sync_on_boot should return True (in sync)
        assert verify_index_sync_on_boot(kv) is True

        # Manually remove from BM25 (simulate dirty restart)
        functions._bm25_index.remove(obs_id)
        assert functions._bm25_index.size == 0

        # verify_index_sync_on_boot should detect mismatch and return False
        assert verify_index_sync_on_boot(kv) is False
