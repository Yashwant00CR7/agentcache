"""
Shared pytest fixtures for agentcache test suite.
"""

import pytest

import agentcache.app as app_mod
from agentcache.app import create_app
from agentcache.db import StateKV


@pytest.fixture
def tmp_db(tmp_path):
    """Return a fresh StateKV instance backed by a temporary SQLite file."""
    db_path = str(tmp_path / "test_store.db")
    return StateKV(db_path=db_path)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """
    Reset all agentcache.app module-level globals, set AGENTCACHE_DB_PATH to a temp db,
    and yield a Flask test client.
    """
    db_path = str(tmp_path / "app_client.db")
    monkeypatch.setenv("AGENTCACHE_DB_PATH", db_path)

    app_mod.kv = None
    app_mod.search_service = None
    app_mod.observation_store = None

    app = create_app()
    with app.test_client() as client:
        yield client


@pytest.fixture
def authed_client(tmp_path, monkeypatch):
    """
    Reset all agentcache.app module-level globals, set AGENTCACHE_SECRET=test-secret
    and AGENTCACHE_DB_PATH to a temp db, call create_app(), and yield (client, "test-secret").
    """
    db_path = str(tmp_path / "authed_client.db")
    secret = "test-secret"
    monkeypatch.setenv("AGENTCACHE_DB_PATH", db_path)
    monkeypatch.setenv("AGENTCACHE_SECRET", secret)

    app_mod.kv = None
    app_mod.search_service = None
    app_mod.observation_store = None

    app = create_app()
    with app.test_client() as client:
        yield client, secret
