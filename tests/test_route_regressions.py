"""
Route regression tests for the blueprint split (Task A1.3).

Validates that every endpoint from all 7 blueprints is reachable and returns
the expected HTTP status codes after the monolithic app.py was split into
src/routes/{observations, memories, search, graph, health, mcp, migration}.py.

Tests use the Flask test client — no running server required.
The AGENTCACHE_SECRET env var is NOT set so all auth checks pass through.
"""

import datetime
import json
import os

import pytest

# Ensure src/ is on the path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flask_app(tmp_path_factory):
    """
    Create a fully-configured Flask application using create_app().

    Uses a temp SQLite database so the test suite never touches the real DB.
    Background workers are started but are daemon threads — they exit when
    the process exits; this does not affect test correctness.
    """
    tmp_dir = tmp_path_factory.mktemp("route_regression_db")
    db_path = str(tmp_dir / "test.db")

    # Point the server at an isolated DB
    os.environ["AGENTCACHE_DB_PATH"] = db_path
    # Ensure no auth requirement during tests
    os.environ.pop("AGENTCACHE_SECRET", None)
    os.environ.pop("AGENTMEMORY_SECRET", None)

    import agentcache.app as app_module

    os.environ.pop("AGENTCACHE_SECRET", None)
    os.environ.pop("AGENTMEMORY_SECRET", None)
    from agentcache.db import StateKV

    # Patch StateKV to use tmp db before create_app() initialises it
    original_init = StateKV.__init__

    def patched_init(self, db_path=None, **kwargs):
        original_init(self, db_path=str(tmp_dir / "test.db"), **kwargs)

    StateKV.__init__ = patched_init
    flask_application = app_module.create_app()
    StateKV.__init__ = original_init

    flask_application.config["TESTING"] = True
    return flask_application


@pytest.fixture(scope="module")
def client(flask_app):
    """Return a Flask test client bound to the module-scoped app."""
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _post_json(client, url, payload):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
    )


# ===========================================================================
# Blueprint 1: health.py
#   GET /agentcache/livez
#   GET /agentcache/health
#   GET /agentcache/audit
#   GET /agentcache/config/flags
# ===========================================================================


class TestHealthBlueprint:
    def test_livez_no_auth_required(self, client):
        """GET /livez must respond 200 without any auth token (always open)."""
        resp = client.get("/agentcache/livez")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "service" in data

    def test_livez_returns_service_name(self, client):
        resp = client.get("/agentcache/livez")
        data = resp.get_json()
        assert data["service"] == "agentcache"

    def test_health_returns_200(self, client):
        resp = client.get("/agentcache/health")
        assert resp.status_code == 200
        data = resp.get_json()
        # Folder-based health check fields (REQ-047)
        assert "folderCount" in data
        assert "observationCount" in data
        assert "memoryCount" in data

    def test_audit_returns_200(self, client):
        resp = client.get("/agentcache/audit")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entries" in data

    def test_config_flags_returns_200(self, client):
        resp = client.get("/agentcache/config/flags")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "flags" in data
        assert "version" in data


# ===========================================================================
# Blueprint 2: observations.py
#   POST /agentcache/observe
#   POST /agentcache/agent/observe
#   GET  /agentcache/folders
#   GET  /agentcache/folder/observations
# ===========================================================================


class TestObservationsBlueprint:
    def test_agent_observe_valid_payload(self, client):
        """POST /agent/observe with valid payload returns 201 with observationId."""
        payload = {
            "folderPath": "/home/user/proj-test",
            "agentId": "kiro",
            "text": "Added a new feature to the app",
            "timestamp": _now_iso(),
        }
        resp = _post_json(client, "/agentcache/agent/observe", payload)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "observationId" in data
        assert data["observationId"].startswith("fobs_")

    def test_agent_observe_missing_folder_path_returns_400(self, client):
        """POST /agent/observe missing folderPath returns 400."""
        payload = {"agentId": "kiro", "text": "some work", "timestamp": _now_iso()}
        resp = _post_json(client, "/agentcache/agent/observe", payload)
        assert resp.status_code == 400

    def test_agent_observe_missing_agent_id_returns_400(self, client):
        """POST /agent/observe missing agentId returns 400."""
        payload = {
            "folderPath": "/home/user/proj",
            "text": "some work",
            "timestamp": _now_iso(),
        }
        resp = _post_json(client, "/agentcache/agent/observe", payload)
        assert resp.status_code == 400

    def test_agent_observe_missing_text_returns_400(self, client):
        """POST /agent/observe missing text returns 400."""
        payload = {
            "folderPath": "/home/user/proj",
            "agentId": "kiro",
            "timestamp": _now_iso(),
        }
        resp = _post_json(client, "/agentcache/agent/observe", payload)
        assert resp.status_code == 400

    def test_folders_list_returns_200(self, client):
        """GET /folders returns 200 with a folders list."""
        # Seed at least one observation first
        _post_json(
            client,
            "/agentcache/agent/observe",
            {
                "folderPath": "/home/user/proj-folders-test",
                "agentId": "kiro",
                "text": "Folders test observation",
                "timestamp": _now_iso(),
            },
        )
        resp = client.get("/agentcache/folders")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "folders" in data
        assert isinstance(data["folders"], list)

    def test_folder_observations_returns_200(self, client):
        """GET /folder/observations with valid params returns 200."""
        # Seed an observation first
        fp = "/home/user/proj-obs-test"
        _post_json(
            client,
            "/agentcache/agent/observe",
            {
                "folderPath": fp,
                "agentId": "kiro",
                "text": "Obs for folder/observations test",
                "timestamp": _now_iso(),
            },
        )
        resp = client.get(
            "/agentcache/folder/observations?folderPath=home/user/proj-obs-test&agentId=kiro"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "observations" in data

    def test_folder_observations_missing_params_returns_400(self, client):
        """GET /folder/observations without required params returns 400."""
        resp = client.get("/agentcache/folder/observations")
        assert resp.status_code == 400

    def test_legacy_observe_endpoint_returns_400_or_201(self, client):
        """POST /observe endpoint exists (legacy hook) — doesn't 404."""
        payload = {
            "folderPath": "/home/user/proj",
            "agentId": "kiro",
            "text": "legacy observe call",
            "timestamp": _now_iso(),
        }
        resp = _post_json(client, "/agentcache/observe", payload)
        # The legacy endpoint exists — expect either success or a controlled error, never 404
        assert resp.status_code != 404


# ===========================================================================
# Blueprint 3: memories.py
#   POST /agentcache/remember
#   POST /agentcache/agent/remember
#   GET  /agentcache/memories
#   POST /agentcache/forget
# ===========================================================================


class TestMemoriesBlueprint:
    def test_remember_valid_payload(self, client):
        """POST /remember with valid payload returns 201."""
        payload = {
            "content": "Always use parameterised queries for SQL",
            "type": "fact",
            "concepts": ["sql", "security"],
        }
        resp = _post_json(client, "/agentcache/remember", payload)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "memory" in data

    def test_agent_cache_valid_payload(self, client):
        """POST /agent/remember with content returns 201."""
        payload = {
            "content": "The project uses SQLite with WAL mode",
            "agentId": "kiro",
            "type": "architecture",
            "concepts": ["sqlite", "wal"],
        }
        resp = _post_json(client, "/agentcache/agent/remember", payload)
        assert resp.status_code == 201

    def test_agent_cache_missing_content_returns_400(self, client):
        """POST /agent/remember without content returns 400."""
        resp = _post_json(client, "/agentcache/agent/remember", {"agentId": "kiro"})
        assert resp.status_code == 400

    def test_memories_list_returns_200(self, client):
        """GET /memories returns 200 with memories list."""
        resp = client.get("/agentcache/memories")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "memories" in data
        assert isinstance(data["memories"], list)

    def test_memories_list_latest_only(self, client):
        """GET /memories?latest=true filters to only latest memories."""
        resp = client.get("/agentcache/memories?latest=true")
        assert resp.status_code == 200
        data = resp.get_json()
        # All returned memories should be latest=True or isLatest not False
        for mem in data["memories"]:
            assert mem.get("isLatest") is not False

    def test_forget_memory_by_id(self, client):
        """POST /forget with memoryId deletes a global memory."""
        # Create a memory first
        create_resp = _post_json(
            client,
            "/agentcache/remember",
            {
                "content": "This memory will be forgotten",
                "type": "fact",
            },
        )
        mem_id = create_resp.get_json()["memory"]["id"]
        # Forget it
        resp = _post_json(client, "/agentcache/forget", {"memoryId": mem_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] >= 1

    def test_forget_folder_pair(self, client):
        """POST /forget with folderPath+agentId clears folder observations."""
        # Seed an observation
        fp = "/home/user/proj-to-forget"
        _post_json(
            client,
            "/agentcache/agent/observe",
            {
                "folderPath": fp,
                "agentId": "kiro",
                "text": "Some work that will be forgotten",
                "timestamp": _now_iso(),
            },
        )
        resp = _post_json(
            client,
            "/agentcache/forget",
            {
                "folderPath": fp,
                "agentId": "kiro",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "deleted" in data
        assert data["deleted"] >= 1


# ===========================================================================
# Blueprint 4: search.py
#   POST /agentcache/search
#   POST /agentcache/timeline
# ===========================================================================


class TestSearchBlueprint:
    def test_search_with_query_returns_200(self, client):
        """POST /search with a query returns 200."""
        # Seed something searchable first
        _post_json(
            client,
            "/agentcache/agent/observe",
            {
                "folderPath": "/home/user/proj-search-test",
                "agentId": "kiro",
                "text": "Implemented authentication middleware for the app",
                "timestamp": _now_iso(),
            },
        )
        resp = _post_json(client, "/agentcache/search", {"query": "authentication"})
        assert resp.status_code == 200

    def test_search_missing_query_returns_400(self, client):
        """POST /search without query returns 400."""
        resp = _post_json(client, "/agentcache/search", {})
        assert resp.status_code == 400

    def test_search_with_folder_filter(self, client):
        """POST /search with folderPath filter returns 200."""
        resp = _post_json(
            client,
            "/agentcache/search",
            {
                "query": "test",
                "folderPath": "/home/user/proj-search-test",
                "agentId": "kiro",
            },
        )
        assert resp.status_code == 200

    def test_timeline_returns_200(self, client):
        """POST /timeline returns 200 with observations list."""
        resp = _post_json(client, "/agentcache/timeline", {})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "observations" in data
        assert isinstance(data["observations"], list)

    def test_timeline_with_filters_returns_200(self, client):
        """POST /timeline with folder/agent filters returns 200."""
        resp = _post_json(
            client,
            "/agentcache/timeline",
            {
                "folderPath": "/home/user/proj-search-test",
                "agentId": "kiro",
                "limit": 10,
            },
        )
        assert resp.status_code == 200

    def test_timeline_results_sorted_descending(self, client):
        """Timeline results are sorted newest-first (REQ-071)."""
        fp = "/home/user/proj-timeline-order"
        # Seed observations with distinct timestamps
        for i in range(3):
            ts = datetime.datetime(2025, 6, 1, 10, i, 0).isoformat() + "Z"
            _post_json(
                client,
                "/agentcache/agent/observe",
                {
                    "folderPath": fp,
                    "agentId": "kiro",
                    "text": f"Observation {i}",
                    "timestamp": ts,
                },
            )
        resp = _post_json(
            client,
            "/agentcache/timeline",
            {
                "folderPath": fp,
                "agentId": "kiro",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        timestamps = [o["timestamp"] for o in data["observations"]]
        assert timestamps == sorted(timestamps, reverse=True)


# ===========================================================================
# Blueprint 5: graph.py
#   GET  /agentcache/graph
#   GET  /agentcache/graph/stats
#   POST /agentcache/graph/query
#   POST /agentcache/graph/build
# ===========================================================================


class TestGraphBlueprint:
    def test_graph_returns_200(self, client):
        """GET /graph returns 200 with nodes and edges."""
        resp = client.get("/agentcache/graph")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data

    def test_graph_stats_returns_200(self, client):
        """GET /graph/stats returns 200."""
        resp = client.get("/agentcache/graph/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data

    def test_graph_query_returns_200(self, client):
        """POST /graph/query returns 200."""
        resp = _post_json(client, "/agentcache/graph/query", {})
        assert resp.status_code == 200

    def test_graph_build_returns_200(self, client):
        """POST /graph/build returns 200."""
        resp = _post_json(client, "/agentcache/graph/build", {})
        assert resp.status_code == 200

    def test_graph_nodes_have_required_fields(self, client):
        """Graph nodes contain id, label, folderPath, agentIds, obsCount, color (REQ-024)."""
        # Seed some data
        _post_json(
            client,
            "/agentcache/agent/observe",
            {
                "folderPath": "/home/user/proj-graph-check",
                "agentId": "kiro",
                "text": "Graph node field check",
                "timestamp": _now_iso(),
            },
        )
        resp = client.get("/agentcache/graph")
        data = resp.get_json()
        if data["nodes"]:
            node = data["nodes"][0]
            for field in ("id", "label", "folderPath", "agentIds", "obsCount", "color"):
                assert field in node, f"Missing field '{field}' on graph node"


# ===========================================================================
# Blueprint 6: mcp.py
#   GET  /agentcache/mcp/tools
#   POST /agentcache/mcp/tools
# ===========================================================================


class TestMcpBlueprint:
    def test_mcp_tools_list_returns_200(self, client):
        """GET /mcp/tools returns 200 with a tools list."""
        resp = client.get("/agentcache/mcp/tools")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) > 0

    def test_mcp_tools_contains_required_tools(self, client):
        """The expected MCP tools are all present in the schema."""
        resp = client.get("/agentcache/mcp/tools")
        tool_names = {t["name"] for t in resp.get_json()["tools"]}
        required_tools = {
            "agent_observe",
            "cache_recall",
            "cache_save",
            "agent_cache",
            "cache_diagnose",
            "cache_forget",
            "cache_export",
            "cache_smart_search",
            "cache_folders",
            "cache_folder_observations",
            "cache_timeline",
        }
        assert required_tools.issubset(tool_names), (
            f"Missing tools: {required_tools - tool_names}"
        )

    def test_mcp_tool_call_agent_observe(self, client):
        """POST /mcp/tools agent_observe returns 200."""
        payload = {
            "name": "agent_observe",
            "arguments": {
                "folderPath": "/home/user/proj-mcp-test",
                "agentId": "kiro",
                "text": "MCP observe test",
                "timestamp": _now_iso(),
            },
        }
        resp = _post_json(client, "/agentcache/mcp/tools", payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "content" in data

    def test_mcp_tool_call_cache_recall(self, client):
        """POST /mcp/tools cache_recall returns 200."""
        resp = _post_json(
            client,
            "/agentcache/mcp/tools",
            {
                "name": "cache_recall",
                "arguments": {"query": "authentication"},
            },
        )
        assert resp.status_code == 200

    def test_mcp_tool_call_cache_diagnose(self, client):
        """POST /mcp/tools cache_diagnose returns folderCount etc."""
        resp = _post_json(
            client,
            "/agentcache/mcp/tools",
            {
                "name": "cache_diagnose",
                "arguments": {},
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        result = json.loads(data["content"][0]["text"])
        assert "folderCount" in result
        assert "observationCount" in result

    def test_mcp_tool_call_cache_folders(self, client):
        """POST /mcp/tools cache_folders returns list."""
        resp = _post_json(
            client,
            "/agentcache/mcp/tools",
            {
                "name": "cache_folders",
                "arguments": {},
            },
        )
        assert resp.status_code == 200

    def test_mcp_tool_call_cache_timeline(self, client):
        """POST /mcp/tools cache_timeline returns list."""
        resp = _post_json(
            client,
            "/agentcache/mcp/tools",
            {
                "name": "cache_timeline",
                "arguments": {"limit": 10},
            },
        )
        assert resp.status_code == 200

    def test_mcp_tool_unknown_name_returns_400(self, client):
        """POST /mcp/tools with unknown tool name returns 400."""
        resp = _post_json(
            client,
            "/agentcache/mcp/tools",
            {
                "name": "nonexistent_tool_xyz",
                "arguments": {},
            },
        )
        assert resp.status_code == 400

    def test_mcp_tool_missing_name_returns_400(self, client):
        """POST /mcp/tools without name returns 400."""
        resp = _post_json(client, "/agentcache/mcp/tools", {"arguments": {}})
        assert resp.status_code == 400


# ===========================================================================
# Blueprint 7: migration.py
#   POST /agentcache/migrate
# ===========================================================================


class TestMigrationBlueprint:
    def test_migrate_dry_run_returns_200(self, client):
        """POST /migrate with dry_run=true returns 200 with counts."""
        resp = _post_json(client, "/agentcache/migrate", {"dry_run": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "migrated_sessions" in data
        assert "migrated_observations" in data
        assert "errors" in data

    def test_migrate_without_body_defaults_to_no_dry_run(self, client):
        """POST /migrate with empty body returns 200."""
        resp = _post_json(client, "/agentcache/migrate", {})
        assert resp.status_code == 200


# ===========================================================================
# Blueprint registration completeness
# ===========================================================================


class TestBlueprintRegistration:
    def test_all_expected_endpoints_registered(self, flask_app):
        """All 7 blueprints contribute at least one route each."""
        registered_rules = {rule.rule for rule in flask_app.url_map.iter_rules()}

        # observations.py
        assert "/agentcache/agent/observe" in registered_rules
        assert "/agentcache/folders" in registered_rules
        assert "/agentcache/folder/observations" in registered_rules

        # memories.py
        assert "/agentcache/remember" in registered_rules
        assert "/agentcache/memories" in registered_rules
        assert "/agentcache/forget" in registered_rules

        # search.py
        assert "/agentcache/search" in registered_rules
        assert "/agentcache/timeline" in registered_rules

        # graph.py
        assert "/agentcache/graph" in registered_rules
        assert "/agentcache/graph/stats" in registered_rules
        assert "/agentcache/graph/query" in registered_rules

        # health.py
        assert "/agentcache/livez" in registered_rules
        assert "/agentcache/health" in registered_rules
        assert "/agentcache/audit" in registered_rules
        assert "/agentcache/config/flags" in registered_rules

        # mcp.py
        assert "/agentcache/mcp/tools" in registered_rules

        # migration.py
        assert "/agentcache/migrate" in registered_rules

    def test_no_routes_return_404_from_blueprints(self, client):
        """None of the expected blueprint routes return 404."""
        get_routes = [
            "/agentcache/livez",
            "/agentcache/health",
            "/agentcache/folders",
            "/agentcache/memories",
            "/agentcache/graph",
            "/agentcache/graph/stats",
            "/agentcache/audit",
            "/agentcache/config/flags",
            "/agentcache/mcp/tools",
        ]
        for route in get_routes:
            resp = client.get(route)
            assert resp.status_code != 404, f"Route {route} returned 404"

    def test_cors_headers_present_on_responses(self, client):
        """CORS after_request hook adds the expected headers."""
        resp = client.get(
            "/agentcache/livez",
            headers={"Origin": "http://localhost:3000"},
        )
        assert resp.status_code == 200
        # The CORS hook in create_app() should have added these headers
        assert "Access-Control-Allow-Headers" in resp.headers
        assert "Access-Control-Allow-Methods" in resp.headers

    def test_auth_returns_401_when_secret_set(self, client, flask_app):
        """When AGENTCACHE_SECRET is configured, missing token returns 401 on protected routes.

        /agentcache/audit requires auth; /agentcache/livez and /agentcache/health are open.
        """
        os.environ["AGENTCACHE_SECRET"] = "test-secret-token"
        try:
            resp = client.get("/agentcache/audit")
            assert resp.status_code == 401
        finally:
            del os.environ["AGENTCACHE_SECRET"]

    def test_auth_passes_with_correct_bearer_token(self, client, flask_app):
        """Correct Bearer token passes auth on protected endpoints."""
        secret = "test-secret-token-correct"
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
        """GET /livez is always unauthenticated, even when secret is set (REQ-057)."""
        os.environ["AGENTCACHE_SECRET"] = "some-secret"
        try:
            resp = client.get("/agentcache/livez")
            assert resp.status_code == 200
        finally:
            del os.environ["AGENTCACHE_SECRET"]
