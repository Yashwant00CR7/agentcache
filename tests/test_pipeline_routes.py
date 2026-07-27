"""Tests for the session pipeline routes and ported core functions (#41)."""

import agentcache.core.llm as llm_mod
from agentcache.core.context_builder import context


def _observe(client, folder, agent, text, ts, importance=5):
    resp = client.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": folder,
            "agentId": agent,
            "text": text,
            "timestamp": ts,
            "importance": importance,
        },
    )
    assert resp.status_code == 201
    return resp.get_json()["observationId"]


# ---------------------------------------------------------------------------
# #45 — context() ported to folder scope + POST /context
# ---------------------------------------------------------------------------


def test_context_requires_scope_and_session(tmp_db):
    import pytest

    with pytest.raises(ValueError):
        context(tmp_db, {"project": "proj"})  # no sessionId
    with pytest.raises(ValueError):
        context(tmp_db, {"sessionId": "s1"})  # no folder scope


def test_context_builds_from_folder_observations(app_client):
    client = app_client
    _observe(
        client,
        "myproj",
        "sess-1",
        "Implemented the folder-scoped context builder",
        "2026-07-24T10:00:00Z",
        importance=8,
    )
    import agentcache.app as app_mod

    result = context(
        app_mod.kv,
        {"sessionId": "sess-1", "project": "myproj", "cwd": "myproj", "budget": 1500},
    )
    assert "context" in result
    assert "folder-scoped context builder" in result["context"]
    assert result["blocks"] >= 1


def test_context_route_returns_context_string(app_client):
    client = app_client
    _observe(
        client,
        "routeproj",
        "sess-r",
        "Wired up the pipeline blueprint route",
        "2026-07-24T10:00:00Z",
        importance=7,
    )
    resp = client.post(
        "/agentcache/context",
        json={"sessionId": "sess-r", "project": "routeproj", "cwd": "routeproj"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "pipeline blueprint route" in body["context"]


def test_context_route_400_on_missing_fields(app_client):
    resp = app_client.post("/agentcache/context", json={"project": "x"})
    assert resp.status_code == 400
