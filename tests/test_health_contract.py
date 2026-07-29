"""Hardened contract tests for the /health and /config/flags endpoints.

Motivation
----------
``test_health_flags.py`` only asserts that ``AGENTCACHE_AUTO_COMPRESS`` is
absent from the flags list and two other keys are present. That check
passes even when:

  * the counts in /health are stale, negative, or missing;
  * the /agentmemory alias returns something different from /agentcache;
  * the ``version`` in /config/flags drifts from pyproject;
  * the flag ordering is scrambled (breaks the viewer's cached indices);
  * a request without auth returns a leaky partial payload instead of 401.

These tests pin the *contract* the viewer and health-checkers rely on.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

import agentcache
from agentcache.core.observation_store import normalize_folder_path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


# ---------------------------------------------------------------------------
# /health contract
# ---------------------------------------------------------------------------

REQUIRED_HEALTH_KEYS = {
    "status",
    "folderCount",
    "agentCount",
    "pairCount",
    "observationCount",
    "memoryCount",
    "bm25IndexSize",
    "vectorIndexSize",
    "dbPath",
    "dbSizeBytes",
    "walSizeBytes",
    "syncStatus",
    "lastSyncAt",
}


def _health(client, path="/agentcache/health"):
    resp = client.get(path)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert isinstance(body, dict)
    return body


def test_health_payload_has_full_contract_shape(app_client):
    body = _health(app_client)
    missing = REQUIRED_HEALTH_KEYS - set(body.keys())
    assert not missing, (
        f"health payload lost required keys: {missing}. Full body: {body}"
    )
    # status must be one of the two known values.
    assert body["status"] in ("ok", "degraded"), f"unknown status: {body['status']!r}"


def test_health_counts_start_at_zero_on_a_fresh_db(app_client):
    body = _health(app_client)
    for key in ("folderCount", "agentCount", "pairCount", "observationCount"):
        assert body[key] == 0, (
            f"fresh app reported nonzero {key}={body[key]}, but no data was ingested"
        )


def test_health_counts_update_after_ingest(app_client):
    """The counts must be truthful — write two observations across two
    agents in one folder, then check counts add up.
    """
    for i, agent in enumerate(("agent-h1", "agent-h2")):
        resp = app_client.post(
            "/agentcache/agent/observe",
            json={
                "folderPath": "src/health-live",
                "agentId": agent,
                "text": f"observation {i}",
                "timestamp": f"2026-07-24T10:0{i}:00Z",
            },
        )
        assert resp.status_code == 201

    body = _health(app_client)
    assert body["folderCount"] == 1, (
        f"1 folder ingested → folderCount should be 1, got {body['folderCount']}"
    )
    assert body["agentCount"] == 2, (
        f"2 agents ingested → agentCount should be 2, got {body['agentCount']}"
    )
    assert body["pairCount"] == 2, (
        f"2 pairs → pairCount should be 2, got {body['pairCount']}"
    )
    assert body["observationCount"] == 2, (
        f"2 observations → observationCount should be 2, got {body['observationCount']}"
    )


def test_health_agentcache_and_agentmemory_return_identical_bodies(app_client):
    """Two aliases MUST return the same payload — the /agentmemory alias
    exists specifically for legacy clients and any drift is a bug.

    ``dbSizeBytes`` and ``walSizeBytes`` may differ by a byte between the
    two reads because they're measured from disk between requests. We
    normalize them out of the diff.
    """
    a = _health(app_client, "/agentcache/health")
    b = _health(app_client, "/agentmemory/health")

    for key in ("dbSizeBytes", "walSizeBytes"):
        a.pop(key, None)
        b.pop(key, None)

    assert a == b, (
        "aliased /health payloads diverged:\n"
        f"  /agentcache/health = {a}\n"
        f"  /agentmemory/health = {b}"
    )


# ---------------------------------------------------------------------------
# /config/flags contract
# ---------------------------------------------------------------------------


def test_flags_response_carries_current_package_version(app_client):
    """The ``version`` field in the flags payload is what the viewer badges
    the header with. It must match the installed package version — otherwise
    the viewer displays a lie.
    """
    body = app_client.get("/agentcache/config/flags").get_json()
    assert body["version"] == _pyproject_version(), (
        f"/config/flags version={body['version']!r} but pyproject is "
        f"{_pyproject_version()!r}"
    )
    # And matches the runtime __version__.
    assert body["version"] == agentcache.__version__


def test_flags_are_ordered_and_shape_locked(app_client):
    """The viewer indexes into the flags array by position. We lock the
    exact order and the required schema of each entry.
    """
    body = app_client.get("/agentcache/config/flags").get_json()
    flags = body["flags"]
    assert isinstance(flags, list)
    keys = [f["key"] for f in flags]
    assert keys == ["GRAPH_EXTRACTION_ENABLED", "CONSOLIDATION_ENABLED"], (
        f"flag order changed — viewer indices will break: {keys}"
    )

    required_flag_keys = {
        "key",
        "label",
        "enabled",
        "default",
        "affects",
        "needsLlm",
        "description",
        "enableHow",
        "docsHref",
    }
    for f in flags:
        missing = required_flag_keys - set(f.keys())
        assert not missing, f"flag {f.get('key')!r} lost fields: {missing}"
        assert isinstance(f["enabled"], bool)
        assert isinstance(f["default"], bool)
        assert isinstance(f["affects"], list) and all(
            isinstance(x, str) for x in f["affects"]
        )
        assert isinstance(f["needsLlm"], bool)
        assert f["docsHref"].startswith("https://"), (
            f"docsHref must be an https URL, got {f['docsHref']!r}"
        )


def test_flags_provider_fields_report_no_llm_when_none_configured(app_client, monkeypatch):
    """With no LLM key set, the response advertises no provider. This is
    what the viewer uses to hide the "run consolidation" button.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    body = app_client.get("/agentcache/config/flags").get_json()
    assert body["provider"] == "noop"
    assert body["embeddingProvider"] == "none"


def test_flags_agentcache_and_agentmemory_return_identical_bodies(app_client):
    a = app_client.get("/agentcache/config/flags").get_json()
    b = app_client.get("/agentmemory/config/flags").get_json()
    assert a == b, (
        "aliased /config/flags payloads diverged:\n"
        f"  /agentcache = {a}\n"
        f"  /agentmemory = {b}"
    )


# ---------------------------------------------------------------------------
# Auth gating on the two endpoints
# ---------------------------------------------------------------------------


def test_health_is_public_even_when_secret_configured(authed_client):
    """/health has no @require_auth — locking that as public contract so
    orchestrators (docker healthcheck, k8s liveness) can reach it without
    a token.
    """
    client, _secret = authed_client
    for path in ("/agentcache/health", "/agentmemory/health"):
        res = client.get(path)
        assert res.status_code == 200, (
            f"{path} rejected unauthenticated request in secured mode"
        )


def test_livez_is_public_and_returns_ok(authed_client):
    client, _secret = authed_client
    res = client.get("/agentcache/livez")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["service"] == "agentcache"


def test_flags_requires_auth_and_full_body_only_authenticated(authed_client):
    """/config/flags IS behind auth — unauthenticated requests must 401
    with no partial leak of the flag configuration.
    """
    client, secret = authed_client
    res_unauth = client.get("/agentcache/config/flags")
    assert res_unauth.status_code == 401
    # 401 body must NOT contain flag names — that would defeat the point of auth.
    body = res_unauth.get_data(as_text=True)
    assert "GRAPH_EXTRACTION_ENABLED" not in body
    assert "CONSOLIDATION_ENABLED" not in body

    res_auth = client.get(
        "/agentcache/config/flags",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert res_auth.status_code == 200
    body_auth = res_auth.get_json()
    keys = [f["key"] for f in body_auth["flags"]]
    assert "GRAPH_EXTRACTION_ENABLED" in keys
    assert "CONSOLIDATION_ENABLED" in keys


# ---------------------------------------------------------------------------
# Cross-endpoint invariant
# ---------------------------------------------------------------------------


def test_health_observation_count_matches_folder_endpoint(app_client):
    """/health's observationCount and the per-folder listing must agree —
    they read from the same underlying data through different paths.
    """
    folder = "src/agree-test"
    for i in range(3):
        resp = app_client.post(
            "/agentcache/agent/observe",
            json={
                "folderPath": folder,
                "agentId": "agent-agree",
                "text": f"row {i}",
                "timestamp": f"2026-07-24T10:0{i}:00Z",
            },
        )
        assert resp.status_code == 201

    listed = app_client.get(
        "/agentcache/folder/observations",
        query_string={
            "folderPath": normalize_folder_path(folder),
            "agentId": "agent-agree",
        },
    ).get_json()["observations"]
    health = _health(app_client)

    assert len(listed) == 3
    assert health["observationCount"] == 3, (
        f"/health said {health['observationCount']} obs but /folder/observations "
        f"returned {len(listed)}"
    )
