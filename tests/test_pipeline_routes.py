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


# ---------------------------------------------------------------------------
# #46 — summarize() ported to folder scope + POST /summarize (map-reduce flush)
# ---------------------------------------------------------------------------


def _fake_summary_xml(title="Work session", narrative="Did A and B."):
    return (
        f"<summary><title>{title}</title>"
        f"<narrative>{narrative}</narrative>"
        f"<concepts><concept>alpha</concept></concepts></summary>"
    )


def test_summarize_requires_scope_and_session(tmp_db):
    import pytest

    with pytest.raises(ValueError):
        llm_mod.summarize(tmp_db, {"project": "proj"})  # no sessionId
    with pytest.raises(ValueError):
        llm_mod.summarize(tmp_db, {"sessionId": "s1"})  # no folder scope


def test_summarize_no_key_returns_error(app_client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import agentcache.app as app_mod

    result = llm_mod.summarize(
        app_mod.kv, {"sessionId": "s1", "project": "p", "cwd": "p"}
    )
    assert result["success"] is False
    assert "GEMINI_API_KEY" in result["error"]


def test_summarize_flushes_folder_observations(app_client, monkeypatch):
    client = app_client
    _observe(client, "sumproj", "sess-s", "Did thing A", "2026-07-24T10:00:00Z", 7)
    _observe(client, "sumproj", "sess-s", "Did thing B", "2026-07-24T11:00:00Z", 7)

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "generate_content", lambda s, p: _fake_summary_xml())

    import agentcache.app as app_mod
    from agentcache.core.kv_scopes import KV
    from agentcache.core.observation_store import normalize_folder_path

    result = llm_mod.summarize(
        app_mod.kv,
        {"sessionId": "sess-s", "project": "sumproj", "cwd": "sumproj"},
    )
    assert result["success"] is True
    assert result["summarized"] == 2

    meta = app_mod.kv.get(
        KV.folder_meta(normalize_folder_path("sumproj"), "sess-s"), "meta"
    )
    assert meta["summary"]["title"] == "Work session"
    assert meta["flushCursor"] == "2026-07-24T11:00:00Z"


def test_summarize_accumulates_bounded_history(app_client, monkeypatch):
    # #53 — each flush appends to meta["summaries"] instead of overwriting, so
    # the consolidate() fact-merger has multiple episodic summaries to work with.
    client = app_client
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_mod,
        "generate_content",
        lambda s, p: _fake_summary_xml(title="Session"),
    )

    import agentcache.app as app_mod
    from agentcache.core.kv_scopes import KV
    from agentcache.core.observation_store import normalize_folder_path

    for i in range(3):
        _observe(client, "histproj", "sess-h", f"Did {i}", f"2026-07-24T1{i}:00:00Z", 7)
        result = llm_mod.summarize(
            app_mod.kv,
            {"sessionId": "sess-h", "project": "histproj", "cwd": "histproj"},
        )
        assert result["summarized"] == 1

    meta = app_mod.kv.get(
        KV.folder_meta(normalize_folder_path("histproj"), "sess-h"), "meta"
    )
    assert len(meta["summaries"]) == 3
    # meta["summary"] still holds the latest for single-slot context injection.
    assert meta["summary"] == meta["summaries"][-1]
    assert meta["summaries"][0]["sessionId"] == "sess-h"


def test_summarize_history_is_capped(app_client, monkeypatch):
    client = app_client
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "generate_content", lambda s, p: _fake_summary_xml())
    monkeypatch.setattr(llm_mod, "SUMMARY_HISTORY_LIMIT", 2)

    import agentcache.app as app_mod
    from agentcache.core.kv_scopes import KV
    from agentcache.core.observation_store import normalize_folder_path

    for i in range(4):
        _observe(
            client, "capproj", "sess-cap", f"Did {i}", f"2026-07-24T1{i}:00:00Z", 7
        )
        llm_mod.summarize(
            app_mod.kv,
            {"sessionId": "sess-cap", "project": "capproj", "cwd": "capproj"},
        )

    meta = app_mod.kv.get(
        KV.folder_meta(normalize_folder_path("capproj"), "sess-cap"), "meta"
    )
    assert len(meta["summaries"]) == 2  # oldest two dropped


def test_summarize_advances_cursor_no_double_flush(app_client, monkeypatch):
    client = app_client
    _observe(client, "cursorproj", "sess-c", "First", "2026-07-24T10:00:00Z", 7)

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "generate_content", lambda s, p: _fake_summary_xml())

    import agentcache.app as app_mod

    first = llm_mod.summarize(
        app_mod.kv,
        {"sessionId": "sess-c", "project": "cursorproj", "cwd": "cursorproj"},
    )
    assert first["summarized"] == 1

    # No new observations since the cursor advanced — nothing to flush.
    second = llm_mod.summarize(
        app_mod.kv,
        {"sessionId": "sess-c", "project": "cursorproj", "cwd": "cursorproj"},
    )
    assert second["success"] is True
    assert second["summarized"] == 0


def test_summarize_route_returns_success(app_client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "generate_content", lambda s, p: _fake_summary_xml())

    client = app_client
    _observe(
        client, "routesum", "sess-rs", "Observed something", "2026-07-24T10:00:00Z", 6
    )

    resp = client.post(
        "/agentcache/summarize",
        json={"sessionId": "sess-rs", "project": "routesum", "cwd": "routesum"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["summarized"] == 1


# ---------------------------------------------------------------------------
# #47 — consolidate() ported to iterate folders instead of sessions
# ---------------------------------------------------------------------------


def _observe_concept(client, folder, agent, text, ts, concept):
    resp = client.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": folder,
            "agentId": agent,
            "text": text,
            "timestamp": ts,
            "importance": 8,
            "concepts": [concept],
        },
    )
    assert resp.status_code == 201


def test_consolidate_reads_folder_observations(app_client, monkeypatch):
    client = app_client
    # Three high-signal observations sharing a concept across two agents.
    _observe_concept(
        client,
        "conproj",
        "ag-1",
        "Auth uses JWT tokens",
        "2026-07-24T10:00:00Z",
        "auth",
    )
    _observe_concept(
        client,
        "conproj",
        "ag-1",
        "JWT refresh flow added",
        "2026-07-24T10:05:00Z",
        "auth",
    )
    _observe_concept(
        client,
        "conproj",
        "ag-2",
        "Auth middleware wired",
        "2026-07-24T10:10:00Z",
        "auth",
    )

    monkeypatch.setattr(
        llm_mod,
        "generate_content",
        lambda s, p: (
            "<memory><type>architecture</type><title>Auth design</title>"
            "<content>The system authenticates via JWT.</content>"
            "<concepts><concept>auth</concept></concepts>"
            "<strength>8</strength></memory>"
        ),
    )

    import agentcache.app as app_mod
    from agentcache.core.kv_scopes import KV

    result = llm_mod.consolidate(app_mod.kv, {}, min_observations=3)
    assert result["success"] is True
    assert result["totalObservations"] == 3
    assert result["consolidated"] >= 1

    titles = [m.get("title") for m in app_mod.kv.list(KV.memories)]
    assert "Auth design" in titles


def test_consolidate_callable_with_no_data(app_client):
    # Graph-build route calls consolidate(kv) with no data — must not raise.
    import agentcache.app as app_mod

    result = llm_mod.consolidate(app_mod.kv)
    assert result["success"] is True
    assert result["consolidated"] == 0  # nothing seeded, below min_observations


def test_consolidate_fact_merger_fires_from_folder_history(app_client, monkeypatch):
    # #53 — the semantic fact-merger fires once the folder-scoped summary history
    # reaches 5 episodes, even on a single folder/agent.
    client = app_client
    for i in range(3):
        _observe_concept(
            client,
            "semproj",
            "ag-s",
            f"Auth detail {i}",
            f"2026-07-24T1{i}:00:00Z",
            "auth",
        )

    import agentcache.app as app_mod
    from agentcache.core.kv_scopes import KV
    from agentcache.core.observation_store import normalize_folder_path

    # Seed 5 episodic summaries into the folder's metadata history.
    meta_scope = KV.folder_meta(normalize_folder_path("semproj"), "ag-s")
    meta = app_mod.kv.get(meta_scope, "meta") or {}
    meta["summaries"] = [
        {
            "title": f"Episode {i}",
            "narrative": "Auth uses JWT tokens across sessions.",
            "concepts": ["auth"],
            "sessionId": "ag-s",
            "createdAt": f"2026-07-24T0{i}:00:00Z",
        }
        for i in range(5)
    ]
    app_mod.kv.set(meta_scope, "meta", meta)

    def _fake(system, prompt):
        if "episodic memories" in system:
            return '<facts><fact confidence="0.9">Auth uses JWT tokens.</fact></facts>'
        if "related observations" in system:
            return (
                "<memory><type>architecture</type><title>Auth</title>"
                "<content>JWT auth.</content><concepts><concept>auth</concept>"
                "</concepts><strength>7</strength></memory>"
            )
        return ""

    monkeypatch.setattr(llm_mod, "generate_content", _fake)

    result = llm_mod.consolidate(app_mod.kv, {}, min_observations=3)
    assert result["success"] is True
    assert result["semantic"]["totalSummaries"] == 5
    assert result["semantic"]["newFacts"] == 1

    facts = [s.get("fact") for s in app_mod.kv.list(KV.semantic)]
    assert "Auth uses JWT tokens." in facts
