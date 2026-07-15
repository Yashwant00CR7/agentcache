"""
tests/test_search.py — C1.3

Tests for SearchIndex, HybridSearch, and synonym expansion.
"""


# ---------------------------------------------------------------------------
# SearchIndex unit tests
# ---------------------------------------------------------------------------


class TestSearchIndex:
    def _make_obs(self, obs_id, title, narrative="", concepts=None, obs_type="other"):
        return {
            "id": obs_id,
            "sessionId": "sess_test",
            "title": title,
            "narrative": narrative,
            "concepts": concepts or [],
            "files": [],
            "type": obs_type,
        }

    def test_add_and_exact_match_returns_rank_one(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        obs = self._make_obs("obs_001", "authentication middleware refactor")
        idx.add(obs)
        results = idx.search("authentication middleware")
        assert len(results) > 0
        assert results[0]["obsId"] == "obs_001"

    def test_prefix_matching(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        obs = self._make_obs("obs_002", "authentication token validation")
        idx.add(obs)
        results = idx.search("authen")
        assert any(r["obsId"] == "obs_002" for r in results)

    def test_synonym_expansion_db_conn(self):
        """'db conn' should find document indexed with 'database connection'."""
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        obs = self._make_obs(
            "obs_003",
            "database connection pooling setup",
            "configure database connection pool",
        )
        idx.add(obs)
        results = idx.search("db conn")
        assert any(r["obsId"] == "obs_003" for r in results)

    def test_remove_document(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        obs = self._make_obs("obs_004", "deploy kubernetes service mesh")
        idx.add(obs)
        idx.remove("obs_004")
        results = idx.search("kubernetes")
        assert not any(r["obsId"] == "obs_004" for r in results)

    def test_empty_index_returns_empty(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        results = idx.search("anything")
        assert results == []

    def test_multiple_docs_rank_order(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        idx.add(
            self._make_obs(
                "obs_a", "authentication login system", "user authentication flow"
            )
        )
        idx.add(
            self._make_obs(
                "obs_b", "database migration script", "run database migration"
            )
        )
        idx.add(
            self._make_obs(
                "obs_c", "deployment pipeline CI", "CI CD pipeline deployment"
            )
        )

        results = idx.search("authentication")
        assert results[0]["obsId"] == "obs_a"

    def test_size_property(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        assert idx.size == 0
        idx.add(self._make_obs("x1", "title one"))
        idx.add(self._make_obs("x2", "title two"))
        assert idx.size == 2
        idx.remove("x1")
        assert idx.size == 1

    def test_clear(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        idx.add(self._make_obs("x1", "something"))
        idx.clear()
        assert idx.size == 0
        assert idx.search("something") == []

    def test_dirty_flag_set_on_add(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        assert idx._dirty is False
        idx.add(self._make_obs("x1", "test dirty flag"))
        assert idx._dirty is True

    def test_dirty_flag_set_on_remove(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        idx.add(self._make_obs("x1", "test remove dirty"))
        idx._dirty = False  # reset manually
        idx.remove("x1")
        assert idx._dirty is True

    def test_dirty_flag_reset_after_restore(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        idx.add(self._make_obs("x1", "test restore"))
        data = idx.serialize_data()
        idx2 = SearchIndex()
        idx2.restore_from_data(data)
        assert idx2._dirty is False

    def test_has_method(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        obs = self._make_obs("obs_has", "has method test")
        assert not idx.has("obs_has")
        idx.add(obs)
        assert idx.has("obs_has")
        idx.remove("obs_has")
        assert not idx.has("obs_has")


# ---------------------------------------------------------------------------
# VectorIndex unit tests
# ---------------------------------------------------------------------------


class TestVectorIndex:
    def test_dirty_flag_on_add(self):
        from agentcache.search import VectorIndex

        vi = VectorIndex()
        assert vi._dirty is False
        vi.add("v1", "sess", [0.1, 0.2, 0.3])
        assert vi._dirty is True

    def test_dirty_flag_on_remove(self):
        from agentcache.search import VectorIndex

        vi = VectorIndex()
        vi.add("v1", "sess", [0.1, 0.2, 0.3])
        vi._dirty = False
        vi.remove("v1")
        assert vi._dirty is True

    def test_dirty_flag_reset_after_restore(self):
        from agentcache.search import VectorIndex

        vi = VectorIndex()
        vi.add("v1", "sess", [0.1, 0.2, 0.3])
        data = vi.serialize_data()
        vi2 = VectorIndex()
        vi2.restore_from_data(data)
        assert vi2._dirty is False


# ---------------------------------------------------------------------------
# HybridSearch in BM25-only mode
# ---------------------------------------------------------------------------


class TestHybridSearchBM25Only:
    def _make_obs(self, obs_id, title, narrative=""):
        return {
            "id": obs_id,
            "sessionId": "sess_hybrid",
            "title": title,
            "narrative": narrative,
            "concepts": [],
            "files": [],
            "type": "other",
        }

    def test_hybrid_bm25_only_returns_same_results_as_search_index(self):
        from agentcache.search import HybridSearch, SearchIndex, VectorIndex

        bm25 = SearchIndex()
        vector = VectorIndex()

        docs = [
            self._make_obs("h1", "authentication middleware implementation"),
            self._make_obs("h2", "database migration scripts"),
            self._make_obs("h3", "deployment kubernetes configuration"),
        ]
        for d in docs:
            bm25.add(d)

        # HybridSearch with no embedding provider — BM25 only
        hybrid = HybridSearch(bm25, vector, None, None)
        bm25_direct = bm25.search("authentication", 10)
        hybrid_results = hybrid.search("authentication", 10)

        bm25_ids = [r["obsId"] for r in bm25_direct]
        hybrid_ids = [r["obsId"] for r in hybrid_results]

        # The same document should appear at the top in both
        assert bm25_ids[0] == hybrid_ids[0]

    def test_hybrid_returns_empty_for_no_matches(self):
        from agentcache.search import HybridSearch, SearchIndex, VectorIndex

        bm25 = SearchIndex()
        hybrid = HybridSearch(bm25, VectorIndex(), None, None)
        assert hybrid.search("zzznomatch", 10) == []


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestSearchIndexSerialization:
    def test_roundtrip_preserves_search_results(self):
        from agentcache.search import SearchIndex

        idx = SearchIndex()
        idx.add(
            {
                "id": "rt_001",
                "sessionId": "sess_rt",
                "title": "serialization round trip test",
                "narrative": "verify that index survives serialize/restore",
                "concepts": ["test"],
                "files": [],
                "type": "other",
            }
        )

        data = idx.serialize_data()
        idx2 = SearchIndex()
        idx2.restore_from_data(data)

        results = idx2.search("serialization round trip")
        assert any(r["obsId"] == "rt_001" for r in results)
