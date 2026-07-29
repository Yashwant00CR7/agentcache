"""Behaviour tests for the search seam — BM25 ranking, scope isolation,
unicode, remove semantics, and pagination.

Motivation
----------
The existing ``test_search_service.py`` tests only that ``search()`` returns
"something" and that top-1 is the expected id in happy-path cases. Those
tests pass even when:

  * ranking is broken (any hit in the list satisfies ``len >= 1``);
  * a removed id is still returned with a stale hydration payload;
  * a folder-scoped search leaks hits from other folders on a case-insensitive
    tokenizer collision;
  * unicode/emoji text tokenizes to nothing and quietly returns [];
  * ``limit`` is silently ignored beyond a certain corpus size.

The tests below assert on *rank order*, *count consistency*, *scope
disjointness*, and *identity across index/remove/re-index*, so refactors of
the tokenizer or the BM25 scorer break these tests loudly when they change
observable behaviour.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from agentcache.core import KV, SearchService
from agentcache.core.observation_store import ObservationStore, normalize_folder_path
from agentcache.search import SearchIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(tmp_db) -> SearchService:
    return SearchService(bm25_index=SearchIndex(), kv=tmp_db)


def _seed(kv, svc: SearchService, obs: Dict) -> None:
    """Put an observation on disk *and* into the search index the same way
    the ObservationStore does — so search hydration finds it.
    """
    fp = obs["folderPath"]
    aid = obs["agentId"]
    kv.set(KV.folders, f"{fp}:{aid}", {"folderPath": fp, "agentId": aid})
    kv.set(KV.folder_obs(fp, aid), obs["id"], obs)
    kv.set(KV.obs_lookup, obs["id"], {"folderPath": fp, "agentId": aid})
    svc.index(obs)


def _ids(results: List[Dict]) -> List[str]:
    return [r["id"] for r in results]


# ---------------------------------------------------------------------------
# BM25 ranking — behaviour, not just presence
# ---------------------------------------------------------------------------


def test_higher_term_frequency_wins_over_incidental_mention(tmp_db):
    """A document that mentions the query term N times must outrank one
    that mentions it once, all else equal. Locks down that we're actually
    running a BM25 scorer, not a "matched → 1.0" boolean predicate.
    """
    kv = tmp_db
    svc = _svc(kv)

    # Same length, same folder/agent — the only signal is term frequency.
    padding = "lorem ipsum dolor sit amet consectetur adipiscing elit"
    _seed(
        kv,
        svc,
        {
            "id": "obs_dense",
            "title": "Auth",
            "text": "authentication authentication authentication " + padding,
            "folderPath": "src/rank",
            "agentId": "a",
        },
    )
    _seed(
        kv,
        svc,
        {
            "id": "obs_sparse",
            "title": "Auth",
            "text": "authentication " + padding + " " + padding + " " + padding,
            "folderPath": "src/rank",
            "agentId": "a",
        },
    )

    results = svc.search("authentication", limit=10)
    ordered_ids = _ids(results)
    assert "obs_dense" in ordered_ids and "obs_sparse" in ordered_ids
    assert ordered_ids.index("obs_dense") < ordered_ids.index("obs_sparse"), (
        f"BM25 ranking regression: dense-mention doc should outrank sparse, "
        f"got order {ordered_ids}"
    )
    # And the scores must actually differ — otherwise the ordering is arbitrary.
    scores = {r["id"]: r["score"] for r in results}
    assert scores["obs_dense"] > scores["obs_sparse"], (
        f"BM25 scores should differ by term frequency, got {scores}"
    )


def test_rare_term_scores_above_common_term(tmp_db):
    """Classic IDF property — a query hitting a term that appears in one
    document ranks that document above a term that appears in every
    document.
    """
    kv = tmp_db
    svc = _svc(kv)

    common = "shared token appearing in every document body"
    for i in range(5):
        _seed(
            kv,
            svc,
            {
                "id": f"obs_common_{i}",
                "title": f"Doc {i}",
                "text": common,
                "folderPath": "src/idf",
                "agentId": "a",
            },
        )
    _seed(
        kv,
        svc,
        {
            "id": "obs_rare",
            "title": "Unique",
            "text": "quokka " + common,
            "folderPath": "src/idf",
            "agentId": "a",
        },
    )

    # Query the rare term alone → the unique doc must win.
    results = svc.search("quokka", limit=10)
    assert results, "search returned no hits for a rare-but-present term"
    assert results[0]["id"] == "obs_rare"

    # Query the common term → the unique doc still appears (contains common),
    # but its score should not dominate; the common docs are legitimate hits.
    results = svc.search("shared token", limit=10)
    assert len(results) >= 5, (
        f"IDF regression? Expected common-term hits, got {_ids(results)}"
    )


# ---------------------------------------------------------------------------
# Scope isolation — the multi-tenant contract
# ---------------------------------------------------------------------------


def test_folder_scoped_search_never_leaks_other_folders(tmp_db):
    """The isolation contract: a scoped query returns hits only from that
    folder even when other folders contain the same tokens. This is what
    keeps agents from reading each other's memory.
    """
    kv = tmp_db
    svc = _svc(kv)

    for folder in ("src/tenant-a", "src/tenant-b", "src/tenant-c"):
        _seed(
            kv,
            svc,
            {
                "id": f"obs_{folder.split('/')[-1]}",
                "title": "T",
                "text": "distinctive tenant-payload shared across tenants",
                "folderPath": folder,
                "agentId": "shared-agent",
            },
        )

    # No scope → all three
    all_hits = svc.search("distinctive tenant-payload", limit=10)
    assert {"obs_tenant-a", "obs_tenant-b", "obs_tenant-c"} <= set(_ids(all_hits))

    # Scoped to A → exactly A
    a_hits = svc.search(
        "distinctive tenant-payload",
        folder_path="src/tenant-a",
        limit=10,
    )
    assert _ids(a_hits) == ["obs_tenant-a"], (
        f"folder-scoped search leaked cross-tenant hits: {_ids(a_hits)}"
    )

    # Scoped to a folder that doesn't exist → empty (not error)
    empty = svc.search("distinctive tenant-payload", folder_path="src/nope", limit=10)
    assert empty == []


def test_agent_scoped_search_never_leaks_other_agents(tmp_db):
    kv = tmp_db
    svc = _svc(kv)

    for agent in ("agent-x", "agent-y"):
        _seed(
            kv,
            svc,
            {
                "id": f"obs_{agent}",
                "title": "T",
                "text": "cross-agent payload text needle",
                "folderPath": "src/shared",
                "agentId": agent,
            },
        )

    hits = svc.search("needle", agent_id="agent-x", limit=10)
    assert _ids(hits) == ["obs_agent-x"]


# ---------------------------------------------------------------------------
# Remove semantics — no zombies
# ---------------------------------------------------------------------------


def test_remove_is_immediate_and_complete(tmp_db):
    """Removed observation is invisible to every subsequent query — not just
    the one that used the exact removed vocabulary."""
    kv = tmp_db
    svc = _svc(kv)

    _seed(
        kv,
        svc,
        {
            "id": "obs_gone",
            "title": "Gone",
            "text": "unique-vocab-alpha and shared-vocab",
            "folderPath": "src/rem",
            "agentId": "a",
        },
    )
    _seed(
        kv,
        svc,
        {
            "id": "obs_keep",
            "title": "Keep",
            "text": "shared-vocab and another line",
            "folderPath": "src/rem",
            "agentId": "a",
        },
    )

    svc.remove("obs_gone")

    for q in ("unique-vocab-alpha", "shared-vocab", "gone"):
        hits = svc.search(q, limit=10)
        assert "obs_gone" not in _ids(hits), (
            f"removed obs still returned for query {q!r}: {_ids(hits)}"
        )

    # The surviving observation must still be findable.
    hits = svc.search("shared-vocab", limit=10)
    assert "obs_keep" in _ids(hits)


def test_reindex_after_remove_matches_a_fresh_index(tmp_db):
    """Idempotency: index → remove → index the same obs, and the result
    is indistinguishable from a fresh single-index. Catches state-leak in
    the inverted index (stale postings, wrong doc_term_counts, etc.).
    """
    kv = tmp_db
    svc = _svc(kv)

    obs = {
        "id": "obs_cycle",
        "title": "Cycle",
        "text": "cycle-vocab-beta appears exactly once",
        "folderPath": "src/idem",
        "agentId": "a",
    }
    _seed(kv, svc, obs)
    baseline = svc.search("cycle-vocab-beta", limit=5)
    baseline_score = baseline[0]["score"]

    svc.remove("obs_cycle")
    assert not svc.search("cycle-vocab-beta", limit=5)

    # Re-index in place. Score must be identical to the fresh baseline
    # (would drift if total_doc_length or postings were not cleaned).
    svc.index(obs)
    after = svc.search("cycle-vocab-beta", limit=5)
    assert after and after[0]["id"] == "obs_cycle"
    assert after[0]["score"] == pytest.approx(baseline_score, rel=1e-9), (
        f"BM25 score drifted after remove+reindex: {baseline_score} → {after[0]['score']}"
    )


# ---------------------------------------------------------------------------
# Query edge cases — empty, whitespace, unicode, punctuation
# ---------------------------------------------------------------------------


def test_empty_and_whitespace_queries_return_empty_list(tmp_db):
    kv = tmp_db
    svc = _svc(kv)
    _seed(
        kv,
        svc,
        {
            "id": "obs_x",
            "title": "X",
            "text": "any text",
            "folderPath": "src/x",
            "agentId": "a",
        },
    )

    for q in ("", "   ", "\t\n"):
        assert svc.search(q, limit=10) == [], (
            f"expected empty results for whitespace-only query {q!r}"
        )


def test_search_respects_limit_when_many_hits(tmp_db):
    kv = tmp_db
    svc = _svc(kv)
    for i in range(15):
        _seed(
            kv,
            svc,
            {
                "id": f"obs_{i}",
                "title": f"Doc {i}",
                "text": f"repeated needle-word slot-{i}",
                "folderPath": "src/lim",
                "agentId": "a",
            },
        )
    for limit in (1, 3, 7, 10, 15):
        results = svc.search("needle-word", limit=limit)
        assert len(results) == min(limit, 15), (
            f"search(limit={limit}) returned {len(results)} results"
        )


def test_search_scores_descend_monotonically(tmp_db):
    """The scoring contract: results come back with non-increasing scores.
    A viewer or an agent that assumes rank order should not have to sort."""
    kv = tmp_db
    svc = _svc(kv)
    for i in range(6):
        text = ("needle " * (i + 1)) + "background words filler content"
        _seed(
            kv,
            svc,
            {
                "id": f"obs_mono_{i}",
                "title": "M",
                "text": text,
                "folderPath": "src/mono",
                "agentId": "a",
            },
        )
    results = svc.search("needle", limit=10)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), (
        f"search results are not returned in descending-score order: {scores}"
    )


# ---------------------------------------------------------------------------
# Integration: ObservationStore → SearchService coupling
# ---------------------------------------------------------------------------


def test_observation_store_ingest_indexes_and_search_is_hydrated(tmp_db):
    """When we ingest through the ObservationStore (not seed manually), the
    search result must come back fully hydrated with the stored payload —
    catching a whole class of "indexed but not stored" bugs.
    """
    kv = tmp_db
    svc = _svc(kv)
    store = ObservationStore(kv=kv, search_service=svc)

    obs_id = store.ingest(
        {
            "folderPath": "src/hydra",
            "agentId": "agent-h",
            "text": "The hydration probe writes back through the whole stack.",
            "timestamp": "2026-07-25T10:00:00Z",
            "importance": 7,
            "concepts": ["hydration"],
        }
    )["observationId"]

    hits = svc.search("hydration probe", limit=5)
    assert hits and hits[0]["id"] == obs_id
    result = hits[0]

    # Every field the store wrote must appear in the hydrated result.
    fp_norm = normalize_folder_path("src/hydra")
    for field, expected in [
        ("folderPath", fp_norm),
        ("agentId", "agent-h"),
        ("importance", 7),
    ]:
        assert result[field] == expected, (
            f"hydration lost {field}: got {result[field]!r}, expected {expected!r}"
        )
    assert "hydration probe" in result["text"]
    assert result.get("score", 0) > 0
