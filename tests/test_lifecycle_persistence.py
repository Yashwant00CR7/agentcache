"""Hardened lifecycle tests — every write path must survive a full restart.

Motivation
----------
Tests that keep a single Flask client / SQLite connection alive from ingest
through read miss the entire class of persistence bugs where a write path
buffers in memory (BM25 index, search persistence, meta cache) but never
lands on disk. The lightweight round-trip tests we had ('write and read back
in the same process') can't tell the difference between "actually persisted"
and "still in the in-memory dict".

Seams under test
----------------
1. ``StateKV`` on-disk storage — write with one instance, read with a fresh
   instance on the same path.
2. ``create_app()`` cold-start — write via one Flask app, tear the app
   globals down, rebuild ``create_app()`` on the same ``AGENTCACHE_DB_PATH``,
   verify every read path (HTTP, MCP, search) still returns the data.
3. ``SearchService.load_persisted()`` — the BM25 shard must survive a fresh
   process; a search that fails here is the exact regression that hides
   until a real server restart.

Everything here is intentionally slow and physical: it uses real files on
disk, real second connections, real ``create_app`` calls.
"""

from __future__ import annotations

import os
from typing import Tuple

import pytest

import agentcache.app as app_mod
from agentcache.app import create_app
from agentcache.core.kv_scopes import KV
from agentcache.core.observation_store import normalize_folder_path
from agentcache.core.search_service import SearchService
from agentcache.db import StateKV
from agentcache.search import SearchIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_app_globals() -> None:
    """Force ``create_app()`` to rebuild singletons on the next call."""
    app_mod.kv = None
    app_mod.search_service = None
    app_mod.observation_store = None
    app_mod.embedding_provider = None
    app_mod.persistence = None


def _fresh_app(db_path: str, monkeypatch) -> Tuple:
    """Return a cold-started Flask client bound to ``db_path``.

    ``monkeypatch.setenv`` here targets the outer test's monkeypatch fixture
    so that env-var restoration happens at end of test. The DB workers are
    disabled so background threads don't churn our on-disk state under us.
    """
    monkeypatch.setenv("AGENTCACHE_DB_PATH", db_path)
    monkeypatch.setenv("AGENTCACHE_DISABLE_WORKERS", "true")
    _reset_app_globals()
    app = create_app()
    return app, app.test_client()


def _observe(client, folder: str, agent: str, text: str, ts: str, importance: int = 5) -> str:
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
    assert resp.status_code == 201, (
        f"observe failed: {resp.status_code} {resp.get_data(as_text=True)}"
    )
    return resp.get_json()["observationId"]


# ---------------------------------------------------------------------------
# StateKV — raw-storage restart survival
# ---------------------------------------------------------------------------


def test_statekv_write_persists_across_a_new_instance(tmp_path):
    """A value written via one StateKV must be visible to a *new* StateKV
    on the same file — proves we're not silently in-memory only.

    Note: StateKV.get() injects ``id`` = key when the stored dict is
    missing one (part of the shim contract), so we assert on the values
    excluding that injected field.
    """
    db_path = str(tmp_path / "persist.db")

    writer = StateKV(db_path=db_path)
    writer.set("scope-A", "key-1", {"hello": "world", "n": 3})
    writer.set("scope-A", "key-2", {"other": True})
    writer.set("scope-B", "solo", {"x": 42})
    writer.teardown()

    reader = StateKV(db_path=db_path)
    try:
        got = reader.get("scope-A", "key-1")
        assert got["hello"] == "world" and got["n"] == 3
        got = reader.get("scope-A", "key-2")
        assert got["other"] is True
        got = reader.get("scope-B", "solo")
        assert got["x"] == 42
        # list() must return both entries in scope-A across the boundary.
        listed = reader.list("scope-A")
        assert len(listed) == 2, f"lost rows across restart: {listed!r}"
        # Sanity — each row keeps its stored payload.
        hellos = {e.get("hello") for e in listed}
        others = {e.get("other") for e in listed}
        assert "world" in hellos
        assert True in others
    finally:
        reader.teardown()


def test_statekv_delete_persists_across_a_new_instance(tmp_path):
    """Deletes must persist too — otherwise stale rows resurrect after restart."""
    db_path = str(tmp_path / "delete-persist.db")

    kv1 = StateKV(db_path=db_path)
    kv1.set("scope", "keep", {"v": 1})
    kv1.set("scope", "drop", {"v": 2})
    kv1.delete("scope", "drop")
    kv1.teardown()

    kv2 = StateKV(db_path=db_path)
    try:
        got = kv2.get("scope", "keep")
        assert got is not None and got["v"] == 1
        assert kv2.get("scope", "drop") is None, (
            "deleted row resurrected after restart — DELETE never hit disk"
        )
    finally:
        kv2.teardown()


# ---------------------------------------------------------------------------
# App restart survival — every read path returns the data after cold start.
# ---------------------------------------------------------------------------


def test_observations_survive_full_app_restart(tmp_path, monkeypatch):
    """The end-to-end regression this locks down:

    An agent writes an observation, the process dies (deploy, crash, git
    pull + serve). When it comes back the observation must still be:
      1. Retrievable via GET /folder/observations
      2. Findable via POST /search (BM25 index survived on-disk)
      3. Included in POST /context (context builder reads from KV)
      4. Reflected in GET /agentcache/folders

    If any single link is broken the test fails with a specific message so
    it's obvious which layer regressed.
    """
    db_path = str(tmp_path / "restart.db")
    folder = "src/restart-demo"
    agent = "agent-restart"
    text = "Wired the persistence audit trail with SIGKILL-safe fsync fences."

    # --- Session 1: write via a fresh app ------------------------------------
    _app1, client1 = _fresh_app(db_path, monkeypatch)
    obs_id = _observe(client1, folder, agent, text, "2026-07-24T10:00:00Z", importance=9)

    # Force the search-index shard to flush to disk so we're actually
    # testing "survived a process restart", not "still in the same process".
    app_mod.search_service.flush_persist()
    if app_mod.kv is not None:
        app_mod.kv.teardown()
    _reset_app_globals()

    # --- Session 2: cold-start on the same DB path ---------------------------
    _app2, client2 = _fresh_app(db_path, monkeypatch)

    # 1. Direct list --------------------------------------------------------
    fp_norm = normalize_folder_path(folder)
    resp = client2.get(
        "/agentcache/folder/observations",
        query_string={"folderPath": fp_norm, "agentId": agent},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    listed = resp.get_json()["observations"]
    assert len(listed) == 1, f"lost observation across restart: {listed!r}"
    assert listed[0]["id"] == obs_id
    assert listed[0]["text"] == text, "observation text did not round-trip verbatim"

    # 2. Search -------------------------------------------------------------
    resp = client2.post(
        "/agentcache/search",
        json={"query": "SIGKILL-safe fsync fences", "folderPath": fp_norm, "agentId": agent},
    )
    assert resp.status_code == 200
    results = resp.get_json()
    assert results, "BM25 index did not survive restart — persistence shard lost"
    assert any(r["id"] == obs_id for r in results), (
        f"restart broke the index→id mapping. Results: {results!r}"
    )

    # 3. Context ------------------------------------------------------------
    resp = client2.post(
        "/agentcache/context",
        json={"sessionId": agent, "project": folder, "cwd": folder, "budget": 4000},
    )
    assert resp.status_code == 200
    ctx = resp.get_json()
    assert "SIGKILL-safe fsync fences" in ctx["context"], (
        f"context builder did not re-read persisted observation. Body: {ctx!r}"
    )

    # 4. Folder enumeration --------------------------------------------------
    resp = client2.get("/agentcache/folders")
    assert resp.status_code == 200
    folder_paths = {f.get("folderPath") for f in resp.get_json().get("folders", [])}
    assert fp_norm in folder_paths, (
        f"folder index dropped after restart; got {folder_paths}"
    )


def test_delete_survives_restart_and_does_not_resurrect(tmp_path, monkeypatch):
    """After forget-then-restart the observation must stay gone in every layer.

    The regression this catches: a delete removes from KV but leaves stale
    entries in the search shard, so after restart the search index reload
    re-materialises "ghost" hits pointing at now-missing ids.
    """
    db_path = str(tmp_path / "delete-restart.db")
    folder = "src/delete-demo"
    agent = "agent-delete"

    _app1, client1 = _fresh_app(db_path, monkeypatch)
    keep_id = _observe(client1, folder, agent, "Keeper alpha", "2026-07-24T10:00:00Z")
    drop_id = _observe(client1, folder, agent, "Doomed observation beta", "2026-07-24T10:01:00Z")

    # Forget one observation via the MCP surface — same path an agent uses.
    resp = client1.post(
        "/agentcache/mcp/tools",
        json={
            "name": "cache_forget",
            "arguments": {
                "folderPath": normalize_folder_path(folder),
                "agentId": agent,
                "observationIds": [drop_id],
            },
        },
    )
    assert resp.status_code == 200

    app_mod.search_service.flush_persist()
    if app_mod.kv is not None:
        app_mod.kv.teardown()
    _reset_app_globals()

    _app2, client2 = _fresh_app(db_path, monkeypatch)

    # Delete stuck.
    fp_norm = normalize_folder_path(folder)
    resp = client2.get(
        "/agentcache/folder/observations",
        query_string={"folderPath": fp_norm, "agentId": agent},
    )
    listed_ids = {o["id"] for o in resp.get_json()["observations"]}
    assert keep_id in listed_ids
    assert drop_id not in listed_ids, (
        f"deleted observation resurrected after restart: {drop_id!r}"
    )

    # Search must not return the dropped id via any query — including one
    # against the exact vocabulary of the deleted text.
    resp = client2.post(
        "/agentcache/search",
        json={"query": "Doomed observation beta", "folderPath": fp_norm, "agentId": agent},
    )
    hits = resp.get_json() or []
    assert drop_id not in {h.get("id") for h in hits}, (
        f"stale search index rehydrated the deleted id: {hits!r}"
    )


def test_search_index_persistence_survives_without_app(tmp_path):
    """Bare-metal SearchService round-trip — no Flask, no route wiring.

    Locks down the IndexPersistence contract independently from the HTTP
    layer, so if the shard format ever changes silently, this fails first.
    """
    db_path = str(tmp_path / "search-persist.db")

    kv1 = StateKV(db_path=db_path)
    svc1 = SearchService(bm25_index=SearchIndex(), kv=kv1)
    for i, text in enumerate([
        "Extracted the ObservationStore module for isolation",
        "Refactored SQLite WAL fencing in the persistence layer",
        "Added inkwell-light palette variant for the viewer",
    ]):
        obs = {
            "id": f"obs_p{i}",
            "title": f"Doc {i}",
            "text": text,
            "folderPath": "src/x",
            "agentId": "a",
        }
        kv1.set(KV.memories, obs["id"], obs)
        svc1.index(obs)
    svc1.flush_persist()
    kv1.teardown()

    kv2 = StateKV(db_path=db_path)
    try:
        svc2 = SearchService(bm25_index=SearchIndex(), kv=kv2)
        loaded = svc2.load_persisted()
        assert loaded["bm25"] is True
        assert svc2.bm25_size == 3, (
            f"restart loaded {svc2.bm25_size} rows, expected 3 — "
            "IndexPersistence shard is not complete"
        )

        # Vocabulary-specific queries must still find the right rows.
        cases = {
            "ObservationStore": "obs_p0",
            "SQLite WAL fencing": "obs_p1",
            "inkwell-light": "obs_p2",
        }
        for query, expected_id in cases.items():
            hits = svc2.search(query, limit=5)
            assert hits, f"{query!r} returned no hits after restart"
            assert hits[0]["id"] == expected_id, (
                f"restart search for {query!r} returned "
                f"{hits[0]['id']!r}, expected {expected_id!r}"
            )
    finally:
        kv2.teardown()


def test_meta_and_dedup_survive_restart(tmp_path, monkeypatch):
    """Re-ingesting an identical observation *after restart* must dedup —
    proving the on-disk dedup fingerprint is real and consulted, not just an
    in-memory optimisation.
    """
    db_path = str(tmp_path / "dedup-restart.db")
    folder = "src/dedup-restart"
    agent = "agent-dr"
    text = "Locked down the dedup fingerprint against process boundaries."

    _app1, client1 = _fresh_app(db_path, monkeypatch)
    first_id = _observe(client1, folder, agent, text, "2026-07-24T10:00:00Z")

    app_mod.search_service.flush_persist()
    app_mod.kv.teardown()
    _reset_app_globals()

    _app2, client2 = _fresh_app(db_path, monkeypatch)

    # Same text, later timestamp — must return the *original* id and mark
    # deduplicated=True, otherwise the on-disk dedup shard is missing.
    resp = client2.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": folder,
            "agentId": agent,
            "text": text,
            "timestamp": "2026-07-24T11:00:00Z",
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["observationId"] == first_id, (
        f"dedup broken across restart: got new id {body['observationId']!r}, "
        f"expected reuse of {first_id!r}"
    )
    assert body.get("deduplicated") is True, (
        "restart lost the dedup fingerprint — same text was re-ingested as new"
    )

    # And the folder listing must still show exactly one observation.
    fp_norm = normalize_folder_path(folder)
    resp = client2.get(
        "/agentcache/folder/observations",
        query_string={"folderPath": fp_norm, "agentId": agent},
    )
    assert len(resp.get_json()["observations"]) == 1


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    """Every test in this file gets a clean app_mod slate + no leaked env."""
    yield
    _reset_app_globals()
    # Restore default max cap in case a test set it and pytest reused this fixture.
    for k in ("AGENTCACHE_DB_PATH", "AGENTCACHE_DISABLE_WORKERS", "MAX_OBS_PER_FOLDER"):
        os.environ.pop(k, None)
