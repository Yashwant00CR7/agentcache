"""Hardened tests for ``ObservationStore.ingest`` concurrency and caps.

Motivation
----------
``test_observation_store.py`` covers the happy paths for dedup and
``MAX_OBS_PER_FOLDER``, but assumes single-threaded, single-request
callers. In production the same store is hit from:

  * background workers,
  * multiple Flask threads under gunicorn,
  * MCP tool handlers,

any of which can race. The current dedup uses a per-``(folder, agent)``
mutex; these tests hammer it hard enough that a regression to unlocked
double-insert would blow up.

The cap-boundary test locks down exact off-by-one behaviour: with
``MAX_OBS_PER_FOLDER=N``, insert #N must succeed and insert #N+1 must fail.
The existing test only covered "N+1 fails" without asserting on the exact
count at the boundary.
"""

from __future__ import annotations

import threading
from typing import Dict, List

import pytest

from agentcache.core import KV, ObservationStore, SearchService
from agentcache.core.observation_store import normalize_folder_path
from agentcache.search import SearchIndex

# ---------------------------------------------------------------------------
# Cap boundary
# ---------------------------------------------------------------------------


def test_cap_boundary_is_exact(tmp_db, monkeypatch):
    """With cap = 3, we get exactly 3 stored — no fewer (spurious ValueError),
    no more (off-by-one). The next call raises.
    """
    monkeypatch.setenv("MAX_OBS_PER_FOLDER", "3")
    store = ObservationStore(kv=tmp_db)

    for i in range(3):
        store.ingest(
            {
                "folderPath": "src/cap-exact",
                "agentId": "a",
                "text": f"row {i}",
                "timestamp": f"2026-07-24T10:0{i}:00Z",
            }
        )

    stored = tmp_db.list(KV.folder_obs("src/cap-exact", "a"))
    assert len(stored) == 3, (
        f"cap boundary off — expected 3 stored at the limit, got {len(stored)}"
    )

    with pytest.raises(ValueError, match="Folder observation limit reached"):
        store.ingest(
            {
                "folderPath": "src/cap-exact",
                "agentId": "a",
                "text": "row over cap",
                "timestamp": "2026-07-24T10:03:00Z",
            }
        )

    # Post-error state must be untouched — no partial write, no phantom
    # meta bump.
    stored_after = tmp_db.list(KV.folder_obs("src/cap-exact", "a"))
    assert len(stored_after) == 3, "cap-hit ingest left partial state behind"

    meta = tmp_db.get(KV.folder_meta("src/cap-exact", "a"), "meta")
    assert meta["obsCount"] == 3, (
        f"cap-hit ingest incremented obsCount: {meta['obsCount']} (expected 3)"
    )


def test_cap_zero_disables_check(tmp_db, monkeypatch):
    """MAX_OBS_PER_FOLDER=0 must mean "no cap" — matches the ``> 0`` guard
    in ObservationStore.ingest. Locking that as a public contract.
    """
    monkeypatch.setenv("MAX_OBS_PER_FOLDER", "0")
    store = ObservationStore(kv=tmp_db)
    for i in range(20):
        store.ingest(
            {
                "folderPath": "src/nocap",
                "agentId": "a",
                "text": f"row {i}",
                "timestamp": f"2026-07-24T10:00:{i:02d}Z",
            }
        )
    assert len(tmp_db.list(KV.folder_obs("src/nocap", "a"))) == 20


def test_dedup_never_counts_against_cap(tmp_db, monkeypatch):
    """A duplicated ingest at the cap boundary must return dedup=True
    without raising cap-exceeded. Otherwise a chatty agent silently DoS's
    itself by re-posting the same fact.
    """
    monkeypatch.setenv("MAX_OBS_PER_FOLDER", "2")
    store = ObservationStore(kv=tmp_db)

    store.ingest(
        {
            "folderPath": "src/capdup",
            "agentId": "a",
            "text": "row one",
            "timestamp": "2026-07-24T10:00:00Z",
        }
    )
    store.ingest(
        {
            "folderPath": "src/capdup",
            "agentId": "a",
            "text": "row two",
            "timestamp": "2026-07-24T10:01:00Z",
        }
    )
    # Duplicate of row-one — must dedup, not raise.
    res = store.ingest(
        {
            "folderPath": "src/capdup",
            "agentId": "a",
            "text": "row one",
            "timestamp": "2026-07-24T10:02:00Z",
        }
    )
    assert res.get("deduplicated") is True
    # Still exactly 2 rows on disk.
    assert len(tmp_db.list(KV.folder_obs("src/capdup", "a"))) == 2


# ---------------------------------------------------------------------------
# Dedup race — concurrent ingest of identical text
# ---------------------------------------------------------------------------


def _worker(store: ObservationStore, payload: Dict, results: List, start: threading.Event) -> None:
    start.wait(timeout=5.0)
    try:
        results.append(store.ingest(payload))
    except Exception as exc:  # noqa: BLE001
        results.append({"error": type(exc).__name__, "msg": str(exc)})


def test_concurrent_dedup_yields_single_stored_observation(tmp_db):
    """20 threads race to ingest the same text into the same pair.
    Contract: exactly one observation on disk, all 20 responses reference
    that same id, and no worker crashes.
    """
    kv = tmp_db
    svc = SearchService(bm25_index=SearchIndex(), kv=kv)
    store = ObservationStore(kv=kv, search_service=svc)

    payload = {
        "folderPath": "src/race",
        "agentId": "agent-race",
        "text": "The one observation that should end up on disk exactly once.",
        "timestamp": "2026-07-24T10:00:00Z",
    }

    results: List[Dict] = []
    start = threading.Event()
    threads = [
        threading.Thread(target=_worker, args=(store, payload, results, start))
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=15.0)

    # No worker died.
    assert all("error" not in r for r in results), (
        f"worker exception under dedup race: {[r for r in results if 'error' in r]}"
    )

    # Exactly one row on disk.
    stored = kv.list(KV.folder_obs("src/race", "agent-race"))
    assert len(stored) == 1, (
        f"race produced {len(stored)} stored rows — dedup lock is not effective"
    )
    only_id = stored[0]["id"]

    # All 20 responses reference that same id.
    returned_ids = {r["observationId"] for r in results}
    assert returned_ids == {only_id}, (
        f"race responses split across ids: {returned_ids}, disk has {only_id}"
    )

    # At least 19 of the 20 must be marked deduplicated (one of the winners
    # writes fresh, all others must see dedup).
    dup_count = sum(1 for r in results if r.get("deduplicated") is True)
    assert dup_count == 19, (
        f"expected 19 dedup responses, got {dup_count}. Full results: {results}"
    )


def test_concurrent_distinct_ingests_all_land(tmp_db):
    """Independent texts don't dedup against each other under concurrency —
    all N end up stored. Guards against an over-broad lock that would
    accidentally block distinct writes.
    """
    kv = tmp_db
    svc = SearchService(bm25_index=SearchIndex(), kv=kv)
    store = ObservationStore(kv=kv, search_service=svc)

    N = 20
    results: List[Dict] = []
    start = threading.Event()
    threads = []
    for i in range(N):
        payload = {
            "folderPath": "src/multi",
            "agentId": "agent-m",
            "text": f"distinct observation body number {i}",
            "timestamp": f"2026-07-24T10:00:{i:02d}Z",
        }
        threads.append(
            threading.Thread(target=_worker, args=(store, payload, results, start))
        )
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=15.0)

    assert all("error" not in r for r in results), results
    ids = {r["observationId"] for r in results if "observationId" in r}
    assert len(ids) == N, f"expected {N} unique ids, got {len(ids)}"
    stored = kv.list(KV.folder_obs("src/multi", "agent-m"))
    assert len(stored) == N, f"expected {N} on disk, got {len(stored)}"


def test_concurrent_distinct_pairs_do_not_block_each_other(tmp_db):
    """Two folders being ingested to in parallel must not serialize on a
    shared lock — each pair has its own dedup lock in the store.

    We can't measure timing reliably in CI, but we can at least prove
    correctness: both pairs end up with the expected rows and no
    cross-contamination.
    """
    kv = tmp_db
    store = ObservationStore(kv=kv)

    results: List[Dict] = []
    start = threading.Event()
    ts = "2026-07-24T10:00:00Z"

    def _many(folder: str, agent: str, n: int) -> None:
        start.wait(timeout=5.0)
        for i in range(n):
            try:
                r = store.ingest(
                    {
                        "folderPath": folder,
                        "agentId": agent,
                        "text": f"{folder}-{agent}-{i}",
                        "timestamp": ts,
                    }
                )
                results.append({"pair": (folder, agent), "res": r})
            except Exception as exc:  # noqa: BLE001
                results.append({"error": type(exc).__name__})

    threads = [
        threading.Thread(target=_many, args=("src/pair-a", "agent-1", 10)),
        threading.Thread(target=_many, args=("src/pair-b", "agent-2", 10)),
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=15.0)

    assert all("error" not in r for r in results), results

    a_rows = kv.list(KV.folder_obs("src/pair-a", "agent-1"))
    b_rows = kv.list(KV.folder_obs("src/pair-b", "agent-2"))
    assert len(a_rows) == 10, f"pair-a lost rows: {len(a_rows)}"
    assert len(b_rows) == 10, f"pair-b lost rows: {len(b_rows)}"

    # Cross-scope leakage check: no pair-a rows should appear in pair-b's scope.
    for r in a_rows:
        assert r["folderPath"] == "src/pair-a"
    for r in b_rows:
        assert r["folderPath"] == "src/pair-b"


# ---------------------------------------------------------------------------
# Timeline ordering under rapid inserts
# ---------------------------------------------------------------------------


def test_timeline_ordering_preserved_across_rapid_inserts(tmp_db):
    """Timeline must return newest-first regardless of insert order.
    Inserts with mixed timestamps go in; we assert the returned order is
    strictly monotonic descending.
    """
    store = ObservationStore(kv=tmp_db)

    # Insert in an intentionally scrambled order.
    for stamp in (
        "2026-07-24T10:00:00Z",
        "2026-07-24T09:00:00Z",
        "2026-07-24T11:30:00Z",
        "2026-07-24T08:45:00Z",
        "2026-07-24T12:00:00Z",
        "2026-07-24T10:30:00Z",
    ):
        store.ingest(
            {
                "folderPath": "src/order",
                "agentId": "a",
                "text": f"obs at {stamp}",
                "timestamp": stamp,
            }
        )

    tl = store.timeline(limit=100, folder_path=normalize_folder_path("src/order"), agent_id="a")
    assert len(tl) == 6
    timestamps = [row["timestamp"] for row in tl]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"timeline is not sorted newest-first: {timestamps}"
    )
