"""
tests/test_api.py — C3.1

Integration tests for REST endpoints using the Flask test client.
"""
import sys
import os
import json
import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def flask_app(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("api_test_db")
    db_path = str(tmp_dir / "test.db")
    os.environ.pop("AGENTCACHE_SECRET", None)
    os.environ.pop("AGENTMEMORY_SECRET", None)

    from db import StateKV
    original_init = StateKV.__init__

    def patched_init(self, db_path_arg=None, **kwargs):
        original_init(self, db_path=db_path, **kwargs)

    StateKV.__init__ = patched_init
    import app as app_module
    os.environ.pop("AGENTCACHE_SECRET", None)
    os.environ.pop("AGENTMEMORY_SECRET", None)
    flask_application = app_module.create_app()
    StateKV.__init__ = original_init
    flask_application.config["TESTING"] = True
    return flask_application


@pytest.fixture(scope="module")
def client(flask_app):
    return flask_app.test_client()


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------------------------
# POST /agentcache/agent/observe
# ---------------------------------------------------------------------------

class TestAgentObserve:
    def test_valid_payload_returns_201(self, client):
        resp = _post(client, "/agentcache/agent/observe", {
            "folderPath": "/home/user/test-project",
            "agentId": "kiro",
            "text": "Implemented new authentication middleware",
            "timestamp": _now(),
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert "observationId" in data
        assert data["observationId"].startswith("fobs_")

    def test_missing_folder_path_returns_400(self, client):
        resp = _post(client, "/agentcache/agent/observe", {
            "agentId": "kiro",
            "text": "Some work",
            "timestamp": _now(),
        })
        assert resp.status_code == 400

    def test_missing_agent_id_returns_400(self, client):
        resp = _post(client, "/agentcache/agent/observe", {
            "folderPath": "/home/user/proj",
            "text": "Some work",
            "timestamp": _now(),
        })
        assert resp.status_code == 400

    def test_missing_text_returns_400(self, client):
        resp = _post(client, "/agentcache/agent/observe", {
            "folderPath": "/home/user/proj",
            "agentId": "kiro",
            "timestamp": _now(),
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /agentcache/search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_with_query_returns_200(self, client):
        # Seed data first
        _post(client, "/agentcache/agent/observe", {
            "folderPath": "/home/user/search-proj",
            "agentId": "kiro",
            "text": "Refactored the authentication system",
            "timestamp": _now(),
        })
        resp = _post(client, "/agentcache/search", {"query": "authentication"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list) or isinstance(data, dict)

    def test_search_missing_query_returns_400(self, client):
        resp = _post(client, "/agentcache/search", {})
        assert resp.status_code == 400

    def test_search_empty_query_returns_400(self, client):
        resp = _post(client, "/agentcache/search", {"query": "   "})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /agentcache/folders
# ---------------------------------------------------------------------------

class TestFolders:
    def test_get_folders_returns_200(self, client):
        # Ensure at least one folder exists from earlier tests
        _post(client, "/agentcache/agent/observe", {
            "folderPath": "/home/user/folders-check",
            "agentId": "kiro",
            "text": "Check folders endpoint",
            "timestamp": _now(),
        })
        resp = client.get("/agentcache/folders")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "folders" in data
        assert isinstance(data["folders"], list)


# ---------------------------------------------------------------------------
# GET /agentcache/health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/agentcache/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "folderCount" in data
        assert "observationCount" in data
        assert "memoryCount" in data

    def test_health_status_ok(self, client):
        resp = client.get("/agentcache/health")
        data = resp.get_json()
        assert data.get("status") in ("ok", "degraded")


# ---------------------------------------------------------------------------
# GET /agentcache/livez
# ---------------------------------------------------------------------------

class TestLivez:
    def test_livez_returns_200_no_auth(self, client):
        resp = client.get("/agentcache/livez")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_livez_open_with_secret_set(self, client):
        os.environ["AGENTCACHE_SECRET"] = "test-secret-123"
        try:
            resp = client.get("/agentcache/livez")
            assert resp.status_code == 200
        finally:
            del os.environ["AGENTCACHE_SECRET"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_protected_endpoint_returns_401_with_wrong_token(self, client):
        os.environ["AGENTCACHE_SECRET"] = "correct-secret"
        try:
            resp = client.get(
                "/agentcache/audit",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 401
        finally:
            del os.environ["AGENTCACHE_SECRET"]

    def test_protected_endpoint_passes_with_correct_token(self, client):
        secret = "my-test-secret-xyz"
        os.environ["AGENTCACHE_SECRET"] = secret
        try:
            resp = client.get(
                "/agentcache/audit",
                headers={"Authorization": f"Bearer {secret}"},
            )
            assert resp.status_code == 200
        finally:
            del os.environ["AGENTCACHE_SECRET"]

    def test_livez_always_open_regardless_of_secret(self, client):
        os.environ["AGENTCACHE_SECRET"] = "any-secret"
        try:
            resp = client.get("/agentcache/livez")
            assert resp.status_code == 200
        finally:
            del os.environ["AGENTCACHE_SECRET"]


# ---------------------------------------------------------------------------
# Additional endpoint smoke tests
# ---------------------------------------------------------------------------

class TestMemoriesEndpoint:
    def test_memories_list_returns_200(self, client):
        resp = client.get("/agentcache/memories")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "memories" in data

    def test_remember_valid_payload_returns_201(self, client):
        resp = _post(client, "/agentcache/remember", {
            "content": "API test memory content",
            "type": "fact",
        })
        assert resp.status_code == 201

    def test_forget_nonexistent_id(self, client):
        resp = _post(client, "/agentcache/forget", {"memoryId": "mem_nonexistent"})
        assert resp.status_code == 200

    def test_graph_endpoint_returns_200(self, client):
        resp = client.get("/agentcache/graph")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data

    def test_mcp_tools_list_returns_200(self, client):
        resp = client.get("/agentcache/mcp/tools")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tools" in data
        tool_names = {t["name"] for t in data["tools"]}
        assert "agent_observe" in tool_names
        assert "cache_recall" in tool_names
