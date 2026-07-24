import pytest
import os
import json
from agentcache.core import KV, ObservationStore, ObservationEvents, SearchService
from agentcache.db import StateKV
from agentcache.search import SearchIndex
from agentcache.app import create_app


def test_observation_events_dataclass():
    """Verify ObservationEvents dataclass has required typed callback lists."""
    events = ObservationEvents()
    assert isinstance(events.on_added, list)
    assert isinstance(events.on_deleted, list)
    assert isinstance(events.on_folder_deleted, list)


def test_observation_store_ingest_and_dedup(tmp_db):
    """Verify ObservationStore.ingest happy path, dedup on write, and events firing."""
    kv = tmp_db
    bm25 = SearchIndex()
    search_svc = SearchService(bm25_index=bm25, kv=kv)

    added_events = []
    events = ObservationEvents(on_added=[lambda ev: added_events.append(ev)])

    store = ObservationStore(kv=kv, search_service=search_svc, events=events)

    payload1 = {
        "folderPath": "src/services",
        "agentId": "agent_alpha",
        "text": "Extracted ObservationStore module",
        "timestamp": "2026-07-24T12:00:00Z",
    }

    res1 = store.ingest(payload1)
    assert "observationId" in res1
    assert "deduplicated" not in res1
    obs_id1 = res1["observationId"]

    # Verify event fired
    assert len(added_events) == 1
    assert added_events[0]["type"] == "folder_observation"
    assert added_events[0]["data"]["id"] == obs_id1

    # Ingest distinct text -> deduplicated must NOT be set
    payload_distinct = {
        "folderPath": "src/services",
        "agentId": "agent_alpha",
        "text": "Distinct second observation text",
        "timestamp": "2026-07-24T12:01:00Z",
    }
    res_distinct = store.ingest(payload_distinct)
    assert "observationId" in res_distinct
    assert "deduplicated" not in res_distinct

    # Ingest duplicate text on same folderPath + agentId
    res2 = store.ingest(payload1)
    assert res2.get("observationId") == obs_id1
    assert res2.get("deduplicated") is True

    # SearchService indexed obs1
    search_results = search_svc.search("Extracted ObservationStore", limit=5)
    assert len(search_results) == 1
    assert search_results[0]["id"] == obs_id1


def test_observation_store_max_cap(tmp_db, monkeypatch):
    """Verify MAX_OBS_PER_FOLDER cap is enforced during ingest."""
    monkeypatch.setenv("MAX_OBS_PER_FOLDER", "2")

    kv = tmp_db
    store = ObservationStore(kv=kv)

    store.ingest({
        "folderPath": "src/cap",
        "agentId": "agent_beta",
        "text": "First item",
        "timestamp": "2026-07-24T12:00:00Z",
    })
    store.ingest({
        "folderPath": "src/cap",
        "agentId": "agent_beta",
        "text": "Second item",
        "timestamp": "2026-07-24T12:01:00Z",
    })

    with pytest.raises(ValueError, match="Folder observation limit reached"):
        store.ingest({
            "folderPath": "src/cap",
            "agentId": "agent_beta",
            "text": "Third item",
            "timestamp": "2026-07-24T12:02:00Z",
        })


def test_observation_store_manual_dedup(tmp_db):
    """Verify ObservationStore.dedup removes duplicate observations across pairs."""
    kv = tmp_db
    store = ObservationStore(kv=kv)

    obs1 = {
        "id": "fobs_dup_1",
        "folderPath": "src/dedup",
        "agentId": "agent_gamma",
        "text": "Duplicated text sample",
        "timestamp": "2026-07-24T10:00:00Z",
    }
    obs2 = {
        "id": "fobs_dup_2",
        "folderPath": "src/dedup",
        "agentId": "agent_gamma",
        "text": "Duplicated text sample",
        "timestamp": "2026-07-24T11:00:00Z",
    }

    kv.set(KV.folders, "src/dedup:agent_gamma", {"folderPath": "src/dedup", "agentId": "agent_gamma"})
    kv.set(KV.folder_obs("src/dedup", "agent_gamma"), "fobs_dup_1", obs1)
    kv.set(KV.folder_obs("src/dedup", "agent_gamma"), "fobs_dup_2", obs2)
    kv.set(KV.obs_lookup, "fobs_dup_1", {"folderPath": "src/dedup", "agentId": "agent_gamma"})
    kv.set(KV.obs_lookup, "fobs_dup_2", {"folderPath": "src/dedup", "agentId": "agent_gamma"})

    res = store.dedup("src/dedup", "agent_gamma")
    assert res["success"] is True
    assert res["deduplicated"] == 1
    assert res["kept"] == 1

    remaining = kv.list(KV.folder_obs("src/dedup", "agent_gamma"))
    assert len(remaining) == 1
    assert remaining[0]["id"] == "fobs_dup_1"


def test_observation_store_forget_full_and_partial(tmp_db):
    """Verify ObservationStore.forget partial and full pair deletions and events."""
    kv = tmp_db
    bm25 = SearchIndex()
    search_svc = SearchService(bm25_index=bm25, kv=kv)

    deleted_obs = []
    deleted_folders = []
    events = ObservationEvents(
        on_deleted=[lambda ids: deleted_obs.extend(ids)],
        on_folder_deleted=[lambda fp, aid: deleted_folders.append((fp, aid))],
    )

    store = ObservationStore(kv=kv, search_service=search_svc, events=events)

    obs1_id = store.ingest({
        "folderPath": "src/forget",
        "agentId": "agent_forget",
        "text": "First item to forget",
        "timestamp": "2026-07-24T10:00:00Z",
    })["observationId"]

    obs2_id = store.ingest({
        "folderPath": "src/forget",
        "agentId": "agent_forget",
        "text": "Second item to keep initially",
        "timestamp": "2026-07-24T11:00:00Z",
    })["observationId"]

    # Explicitly empty observationIds list -> graceful handling of no-op partial delete
    res_noop = store.forget({
        "folderPath": "src/forget",
        "agentId": "agent_forget",
        "observationIds": [],
    })
    assert res_noop["success"] is True
    assert res_noop["deleted"] == 0

    # 1. Partial deletion
    res_part = store.forget({
        "folderPath": "src/forget",
        "agentId": "agent_forget",
        "observationIds": [obs1_id],
    })
    assert res_part["success"] is True
    assert res_part["deleted"] == 1
    assert deleted_obs == [obs1_id]

    meta = kv.get(KV.folder_meta("src/forget", "agent_forget"), "meta")
    assert meta["obsCount"] == 1

    # 2. Full folder pair deletion
    res_full = store.forget({
        "folderPath": "src/forget",
        "agentId": "agent_forget",
    })
    assert res_full["success"] is True
    assert res_full["deleted"] == 1
    assert len(deleted_folders) == 1
    assert deleted_folders[0] == ("src/forget", "agent_forget")

    remaining_folders = kv.list(KV.folders)
    assert len(remaining_folders) == 0


def test_observation_store_timeline_sorting_and_filtering(tmp_db):
    """Verify ObservationStore.timeline sorting descending and timestamp filters."""
    kv = tmp_db
    store = ObservationStore(kv=kv)

    store.ingest({
        "folderPath": "src/t1",
        "agentId": "agent_t",
        "text": "Oldest observation",
        "timestamp": "2026-07-24T10:00:00Z",
    })
    store.ingest({
        "folderPath": "src/t1",
        "agentId": "agent_t",
        "text": "Middle observation",
        "timestamp": "2026-07-24T12:00:00Z",
    })
    store.ingest({
        "folderPath": "src/t2",
        "agentId": "agent_t",
        "text": "Newest observation",
        "timestamp": "2026-07-24T14:00:00Z",
    })

    # Timeline all
    tl_all = store.timeline(limit=10)
    assert len(tl_all) == 3
    assert tl_all[0]["text"] == "Newest observation"
    assert tl_all[1]["text"] == "Middle observation"
    assert tl_all[2]["text"] == "Oldest observation"

    # Filter before and after
    tl_filtered = store.timeline(
        limit=10,
        before="2026-07-24T13:00:00Z",
        after="2026-07-24T11:00:00Z",
    )
    assert len(tl_filtered) == 1
    assert tl_filtered[0]["text"] == "Middle observation"

    # Filter folder_path
    tl_folder = store.timeline(folder_path="src/t1")
    assert len(tl_folder) == 2


def test_observation_store_rebuild_index_and_backfill_lookup(tmp_db):
    """Verify ObservationStore.rebuild_index and backfill_lookup populate missing entries."""
    kv = tmp_db
    bm25 = SearchIndex()
    search_svc = SearchService(bm25_index=bm25, kv=kv)
    store = ObservationStore(kv=kv, search_service=search_svc)

    # 1. Backfill test: insert raw observation without lookup entry
    kv.set(KV.folders, "src/bf:agent_bf", {"folderPath": "src/bf", "agentId": "agent_bf", "obsCount": 1})
    kv.set(KV.folder_obs("src/bf", "agent_bf"), "fobs_bf1", {
        "id": "fobs_bf1",
        "folderPath": "src/bf",
        "agentId": "agent_bf",
        "text": "Unindexed backfill observation",
        "timestamp": "2026-07-24T10:00:00Z",
    })

    kv.delete(KV.obs_lookup, "fobs_bf1")
    assert kv.get(KV.obs_lookup, "fobs_bf1") is None
    store.backfill_lookup()
    lookup = kv.get(KV.obs_lookup, "fobs_bf1")
    assert lookup is not None
    assert lookup["folderPath"] == "src/bf"
    assert lookup["agentId"] == "agent_bf"

    # 2. Rebuild index test: search_svc index is currently 0
    assert search_svc.bm25_size == 0
    count = store.rebuild_index()
    assert count == 1
    assert search_svc.bm25_size == 1

    search_res = search_svc.search("Unindexed backfill", limit=5)
    assert len(search_res) == 1
    assert search_res[0]["id"] == "fobs_bf1"


def test_http_and_mcp_forget_and_timeline_end_to_end(app_client):
    """Verify Flask HTTP timeline & forget routes and MCP tools work end-to-end."""
    client = app_client

    # Ingest observation via HTTP
    resp1 = client.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": "src/mcp_test",
            "agentId": "agent_mcp",
            "text": "Testing MCP tool handlers",
            "timestamp": "2026-07-24T15:00:00Z",
        },
    )
    assert resp1.status_code == 201

    # Timeline via HTTP POST /agentcache/timeline
    resp_tl = client.post(
        "/agentcache/timeline",
        json={"folderPath": "src/mcp_test", "agentId": "agent_mcp"},
    )
    assert resp_tl.status_code == 200
    data_tl = resp_tl.get_json()
    assert len(data_tl["observations"]) == 1

    # Timeline via MCP tool
    resp_mcp_tl = client.post(
        "/agentcache/mcp/tools",
        json={
            "name": "cache_timeline",
            "arguments": {"folderPath": "src/mcp_test", "agentId": "agent_mcp"},
        },
    )
    assert resp_mcp_tl.status_code == 200
    mcp_out = resp_mcp_tl.get_json()["content"][0]["text"]
    assert "Testing MCP tool handlers" in mcp_out

    # Forget via MCP tool
    resp_mcp_forget = client.post(
        "/agentcache/mcp/tools",
        json={
            "name": "cache_forget",
            "arguments": {"folderPath": "src/mcp_test", "agentId": "agent_mcp"},
        },
    )
    assert resp_mcp_forget.status_code == 200

    # Post-forget timeline check
    resp_tl_post = client.post(
        "/agentcache/timeline",
        json={"folderPath": "src/mcp_test", "agentId": "agent_mcp"},
    )
    assert resp_tl_post.status_code == 200
    assert len(resp_tl_post.get_json()["observations"]) == 0


def test_full_lifecycle_e2e_pass(app_client):
    """Full integration pass: observe -> search -> timeline -> forget -> rebuild (HTTP & MCP layers)."""
    client = app_client

    # 1. OBSERVE via MCP
    mcp_obs = client.post(
        "/agentcache/mcp/tools",
        json={
            "name": "agent_observe",
            "arguments": {
                "folderPath": "src/e2e",
                "agentId": "agent_e2e",
                "text": "Refactored observation store completely",
                "timestamp": "2026-07-24T16:00:00Z",
            },
        },
    )
    assert mcp_obs.status_code == 200
    obs_id = json.loads(mcp_obs.get_json()["content"][0]["text"])["observationId"]

    # 2. SEARCH via HTTP
    search_resp = client.post(
        "/agentcache/search",
        json={"query": "observation store", "folderPath": "src/e2e", "agentId": "agent_e2e"},
    )
    assert search_resp.status_code == 200
    search_data = search_resp.get_json()
    assert len(search_data) == 1
    assert search_data[0]["id"] == obs_id

    # 3. TIMELINE via MCP
    timeline_resp = client.post(
        "/agentcache/mcp/tools",
        json={
            "name": "cache_timeline",
            "arguments": {"folderPath": "src/e2e", "agentId": "agent_e2e"},
        },
    )
    assert timeline_resp.status_code == 200
    assert "Refactored observation store" in timeline_resp.get_json()["content"][0]["text"]

    # 4. REBUILD via ObservationStore
    import agentcache.app as app_mod
    obs_store = app_mod.observation_store
    count = obs_store.rebuild_index()
    assert count >= 1

    # 5. FORGET via HTTP / MCP
    forget_resp = client.post(
        "/agentcache/mcp/tools",
        json={
            "name": "cache_forget",
            "arguments": {"folderPath": "src/e2e", "agentId": "agent_e2e"},
        },
    )
    assert forget_resp.status_code == 200


