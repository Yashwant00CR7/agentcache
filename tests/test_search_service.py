import os
import pytest
from agentcache.core import KV, SearchService, IndexPersistence
from agentcache.db import StateKV
from agentcache.search import SearchIndex
from agentcache.app import create_app, init_services, search_service as global_search_service


def test_kv_scopes_import():
    """Verify KV class is importable and defines standard scope keys."""
    assert KV.folders == "mem:folders"
    assert KV.obs_lookup == "mem:obs_lookup"
    assert KV.memories == "mem:memories"
    assert KV.folder_obs("src/core", "agent1") == "mem:folder:src/core:agent1"


def test_search_service_index_remove_and_search(tmp_db):
    """Verify SearchService index, remove, and search operations work end-to-end."""
    kv = tmp_db

    bm25 = SearchIndex()
    svc = SearchService(bm25_index=bm25, kv=kv)

    obs1 = {
        "id": "obs_1",
        "title": "Auth Middleware",
        "text": "Implemented JWT token validation in authentication middleware",
        "folderPath": "src/auth",
        "agentId": "agent_alpha",
    }
    obs2 = {
        "id": "obs_2",
        "title": "Database Connection",
        "text": "SQLite database pool initialization and query optimization",
        "folderPath": "src/db",
        "agentId": "agent_beta",
    }

    # Populate KV store for hydration
    kv.set(KV.folders, "src/auth:agent_alpha", {"folderPath": "src/auth", "agentId": "agent_alpha"})
    kv.set(KV.folder_obs("src/auth", "agent_alpha"), "obs_1", obs1)
    kv.set(KV.obs_lookup, "obs_1", {"folderPath": "src/auth", "agentId": "agent_alpha"})

    kv.set(KV.folders, "src/db:agent_beta", {"folderPath": "src/db", "agentId": "agent_beta"})
    kv.set(KV.folder_obs("src/db", "agent_beta"), "obs_2", obs2)
    kv.set(KV.obs_lookup, "obs_2", {"folderPath": "src/db", "agentId": "agent_beta"})

    # Index observations into SearchService
    svc.index(obs1)
    svc.index(obs2)

    # Search for JWT token validation
    results = svc.search("JWT token", limit=10)
    assert len(results) >= 1
    assert results[0]["id"] == "obs_1"
    assert "score" in results[0]

    # Filtered search by folder_path
    filtered = svc.search("token", folder_path="src/db", limit=10)
    assert len(filtered) == 0

    filtered_auth = svc.search("token", folder_path="src/auth", limit=10)
    assert len(filtered_auth) == 1
    assert filtered_auth[0]["id"] == "obs_1"

    # Filtered search by agent_id (returns only matching agent)
    filtered_agent = svc.search("validation", agent_id="agent_alpha", limit=10)
    assert len(filtered_agent) == 1
    assert filtered_agent[0]["agentId"] == "agent_alpha"

    filtered_wrong_agent = svc.search("validation", agent_id="agent_other", limit=10)
    assert len(filtered_wrong_agent) == 0

    # Remove obs1 and verify it no longer matches
    svc.remove("obs_1")
    post_remove = svc.search("JWT token", limit=10)
    assert len(post_remove) == 0


def test_search_service_persistence(tmp_db):
    """Verify IndexPersistence saves and loads index data sharded in SQLite."""
    kv = tmp_db

    bm25 = SearchIndex()
    svc = SearchService(bm25_index=bm25, kv=kv)

    obs = {
        "id": "obs_persist_1",
        "title": "Cache Eviction",
        "text": "LRU cache eviction policy implemented for high throughput",
    }
    kv.set(KV.memories, "obs_persist_1", obs)
    svc.index(obs)
    svc.flush_persist()

    # Create fresh SearchService and load persisted index
    new_bm25 = SearchIndex()
    new_svc = SearchService(bm25_index=new_bm25, kv=kv)
    loaded = new_svc.load_persisted()

    assert loaded["bm25"] is True
    assert new_svc.bm25_size > 0

    results = new_svc.search("LRU cache", limit=10)
    assert len(results) >= 1

    # Search for a term never indexed returns an empty list (no error/exception)
    non_existent = new_svc.search("nonexistenttermxyz", limit=10)
    assert isinstance(non_existent, list)
    assert len(non_existent) == 0


def test_http_search_routes_end_to_end(app_client):
    """Verify Flask HTTP search endpoints work end-to-end with real SQLite DB."""
    client = app_client

    import agentcache.app as app_mod
    kv = app_mod.kv
    search_svc = app_mod.search_service

    obs = {
        "id": "obs_http_1",
        "title": "API Authentication Strategy",
        "text": "Secure OAuth2 bearer token authorization for external endpoints",
        "folderPath": "src/api",
        "agentId": "agent_gamma",
    }

    kv.set(KV.folders, "src/api:agent_gamma", {"folderPath": "src/api", "agentId": "agent_gamma"})
    kv.set(KV.folder_obs("src/api", "agent_gamma"), "obs_http_1", obs)
    kv.set(KV.obs_lookup, "obs_http_1", {"folderPath": "src/api", "agentId": "agent_gamma"})

    search_svc.index(obs)

    # POST /agentcache/search
    res = client.post("/agentcache/search", json={"query": "OAuth2 bearer token"})
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "obs_http_1"
    assert data[0]["folderPath"] == "src/api"

    # POST /agentmemory/search (alias endpoint)
    res_alias = client.post("/agentmemory/search", json={"query": "OAuth2 bearer token", "limit": 5})
    assert res_alias.status_code == 200
    data_alias = res_alias.get_json()
    assert len(data_alias) == 1
    assert data_alias[0]["id"] == "obs_http_1"

    # Validation paths: missing or empty query -> 400
    res_no_query = client.post("/agentcache/search", json={})
    assert res_no_query.status_code == 400

    res_empty_query = client.post("/agentcache/search", json={"query": ""})
    assert res_empty_query.status_code == 400

    res_alias_no_query = client.post("/agentmemory/search", json={})
    assert res_alias_no_query.status_code == 400

