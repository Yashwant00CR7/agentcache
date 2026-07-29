"""End-to-end lifecycle integration tests for an agent using agentcache.

These tests treat the HTTP surface exactly the way an agent client would:
POST observations, list them back, search across them, build context, list
folders. No mocking, no direct KV pokes — the assertions ride on what the
routes actually return.

The failure mode motivating this file is the class of bug where each layer
tests green in isolation but the whole system doesn't hang together — the
kind of regression that fix #52 (Memory Save and Memory Fetch Internal
Issue) chased down.
"""

from __future__ import annotations

from agentcache.core.observation_store import normalize_folder_path

FOLDER = "src/lifecycle-demo"
AGENT = "agent-lifecycle"


def _observe(client, text: str, timestamp: str, importance: int = 5) -> str:
    resp = client.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": FOLDER,
            "agentId": AGENT,
            "text": text,
            "timestamp": timestamp,
            "importance": importance,
        },
    )
    assert resp.status_code == 201, (
        f"observe failed: {resp.status_code} {resp.get_data(as_text=True)}"
    )
    body = resp.get_json()
    assert "observationId" in body
    return body["observationId"]


def _list_observations(client) -> list[dict]:
    normalized = normalize_folder_path(FOLDER)
    resp = client.get(
        "/agentcache/folder/observations",
        query_string={"folderPath": normalized, "agentId": AGENT},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["observations"]


def test_agent_lifecycle_observe_list_search_context(app_client):
    """Full loop: observe several distinct facts, then find them by every
    means an agent client uses — list, search, context.

    Uses three highly-distinct observation bodies so the search assertions
    can key off exact vocabulary from each one and can't be satisfied by
    accident.
    """
    # ---- ARRANGE: write three observations with independently-verifiable
    #      vocabulary. If any layer drops fields on save, the read-backs
    #      will fail.
    obs_ids = [
        _observe(
            app_client,
            "Migrated authentication middleware to use PyJWT for token validation.",
            "2026-07-20T09:00:00Z",
            importance=8,
        ),
        _observe(
            app_client,
            "Fixed SQLite WAL checkpoint deadlock during high-concurrency writes.",
            "2026-07-21T10:30:00Z",
            importance=9,
        ),
        _observe(
            app_client,
            "Added inkwell-light viewer theme with paper-white surface variant.",
            "2026-07-22T14:15:00Z",
            importance=6,
        ),
    ]
    assert len(obs_ids) == len(set(obs_ids)), (
        "Observation IDs must be unique — collision indicates a broken id "
        f"generator. Got: {obs_ids}"
    )

    # ---- ACT + ASSERT (list): all three observations must round-trip.
    listed = _list_observations(app_client)
    listed_ids = {o["id"] for o in listed}
    assert set(obs_ids).issubset(listed_ids), (
        f"Round-trip lost observations. Wrote {obs_ids}, read back {sorted(listed_ids)}"
    )
    # Text bodies must round-trip verbatim — no truncation, no re-encoding.
    listed_texts = {o["text"] for o in listed if o["id"] in obs_ids}
    assert (
        "Migrated authentication middleware to use PyJWT for token validation."
        in listed_texts
    )
    assert (
        "Fixed SQLite WAL checkpoint deadlock during high-concurrency writes."
        in listed_texts
    )

    # ---- ACT + ASSERT (search): each keyword must return the matching
    #      observation, not just something.
    search_cases = [
        ("PyJWT", "PyJWT"),
        ("SQLite WAL checkpoint", "SQLite WAL checkpoint"),
        ("inkwell-light", "inkwell-light"),
    ]
    for query, expected_vocab in search_cases:
        resp = app_client.post(
            "/agentcache/search",
            json={
                "query": query,
                "folderPath": normalize_folder_path(FOLDER),
                "agentId": AGENT,
                "limit": 10,
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        results = resp.get_json()
        assert isinstance(results, list), f"Search must return a list, got {results!r}"
        assert results, (
            f"Search for {query!r} returned zero hits — indexing didn't "
            "happen or the search route isn't wired to the observation store."
        )
        # The vocabulary from the target observation must appear in at least
        # one hit's text — this can't pass by accident.
        joined = " ".join((r.get("text") or r.get("title") or "") for r in results)
        assert expected_vocab in joined, (
            f"Search for {query!r} returned hits but none contained the "
            f"expected vocabulary {expected_vocab!r}. Results: {results}"
        )

    # ---- ACT + ASSERT (context): building context must incorporate the
    #      high-importance observation content.
    resp = app_client.post(
        "/agentcache/context",
        json={
            "sessionId": AGENT,
            "project": FOLDER,
            "cwd": FOLDER,
            "budget": 4000,
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    ctx = resp.get_json()
    assert "context" in ctx and isinstance(ctx["context"], str)
    assert ctx.get("blocks", 0) >= 1, f"Context must include >=1 block, got {ctx!r}"
    # Highest-importance observations should appear verbatim in the context.
    assert "SQLite WAL checkpoint" in ctx["context"], (
        "Context builder dropped a high-importance observation. "
        f"Context body: {ctx['context']!r}"
    )

    # ---- ACT + ASSERT (folders enumeration): the folder must appear in the
    #      global listing, tying the write path to discovery.
    resp = app_client.get("/agentcache/folders")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    folders_body = resp.get_json()
    assert isinstance(folders_body, dict)
    folder_paths = {f.get("folderPath") for f in folders_body.get("folders", [])}
    assert normalize_folder_path(FOLDER) in folder_paths, (
        f"Written folder is missing from /agentcache/folders listing. "
        f"Got paths: {folder_paths}"
    )


def test_search_scoped_by_folder_does_not_leak_across_folders(app_client):
    """Search restricted to a folder must NOT return hits from other folders.

    Locks down the isolation contract that agents rely on when they observe
    into project-specific folders.
    """
    # Observe the *same distinctive word* into two different folders.
    other_folder = "src/other-folder"

    _observe(
        app_client,
        "Distinctive keyword UNIQUEWORDA in the lifecycle folder.",
        "2026-07-23T10:00:00Z",
    )
    resp = app_client.post(
        "/agentcache/agent/observe",
        json={
            "folderPath": other_folder,
            "agentId": AGENT,
            "text": "Different content UNIQUEWORDA in a totally separate folder.",
            "timestamp": "2026-07-23T10:05:00Z",
            "importance": 5,
        },
    )
    assert resp.status_code == 201

    # Search scoped to the lifecycle folder — must not return the other one.
    resp = app_client.post(
        "/agentcache/search",
        json={
            "query": "UNIQUEWORDA",
            "folderPath": normalize_folder_path(FOLDER),
            "agentId": AGENT,
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    results = resp.get_json()
    assert results, "Scoped search should still find the same-folder hit."
    for r in results:
        text = r.get("text") or ""
        assert "totally separate folder" not in text, (
            f"Scoped search leaked a hit from {other_folder!r}. Result: {r}"
        )
