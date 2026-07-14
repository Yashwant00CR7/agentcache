"""
tests/test_auth.py

Tests for the /auth.md agent onboarding route.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(scope="module")
def flask_app(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("auth_test_db")
    db_path = str(tmp_dir / "test.db")
    os.environ.pop("AGENTCACHE_SECRET", None)
    os.environ.pop("AGENTMEMORY_SECRET", None)

    from db import StateKV

    original_init = StateKV.__init__

    def patched_init(self, db_path_arg=None, **kwargs):
        original_init(self, db_path=db_path, **kwargs)

    StateKV.__init__ = patched_init
    import app as app_module

    flask_application = app_module.create_app()
    StateKV.__init__ = original_init
    flask_application.config["TESTING"] = True
    return flask_application


@pytest.fixture(scope="module")
def client(flask_app):
    return flask_app.test_client()


def test_auth_md_endpoint(client):
    resp = client.get("/auth.md")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/markdown")
    content = resp.data.decode("utf-8")
    assert "# Agent Cache" in content
    assert "Authorization" in content
    assert "agent_observe" in content


def test_auth_md_endpoint_with_secret(flask_app):
    os.environ["AGENTCACHE_SECRET"] = "super-secret-token"
    try:
        client = flask_app.test_client()
        resp = client.get("/auth.md")
        assert resp.status_code == 200
        content = resp.data.decode("utf-8")
        assert "# Agent Cache" in content
    finally:
        os.environ.pop("AGENTCACHE_SECRET", None)
