"""Tests for GET /config/flags — #55.

The stale AGENTCACHE_AUTO_COMPRESS feature flag drove a viewer banner that no
longer applies. Its flag entry (and the whole dead auto-compress chain) was
removed; the surviving flags must keep shipping.
"""


def test_config_flags_omits_auto_compress(app_client):
    resp = app_client.get("/agentcache/config/flags")
    assert resp.status_code == 200

    keys = [f["key"] for f in resp.get_json()["flags"]]
    assert "AGENTCACHE_AUTO_COMPRESS" not in keys
    # The real flags still ship.
    assert "GRAPH_EXTRACTION_ENABLED" in keys
    assert "CONSOLIDATION_ENABLED" in keys
