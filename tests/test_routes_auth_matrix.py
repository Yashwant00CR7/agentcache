"""
Auth matrix — every registered HTTP rule in the Flask app.

For each rule this test asserts:
  1. 401 when a secret is set and no token is provided
  2. non-401 when a valid token is provided
  3. non-401 when no secret is configured (auth disabled mode)

Rules explicitly marked in ``UNPROTECTED_PATHS`` are expected to skip auth
even when the secret is set (livez, health, auth.md, static viewer routes).

Rules that don't take our synthetic empty payload cleanly (e.g. WebSocket,
POST endpoints requiring specific fields) may 4xx/5xx on the *body* — that's
still not a 401, which is all this matrix cares about.
"""

from __future__ import annotations

import pytest

import agentcache.app as app_mod
from agentcache.app import create_app


UNPROTECTED_PATHS = {
    "/auth.md",
    "/agentcache/livez",
    "/agentmemory/livez",
    "/agentcache/health",
    "/agentmemory/health",
    "/",
    "/viewer",
    "/agentcache/viewer",
    "/agentmemory/viewer",
    "/favicon.svg",
}


def _reset_app_globals() -> None:
    app_mod.kv = None
    app_mod.search_service = None
    app_mod.observation_store = None


def _iter_http_rules(app):
    """Yield (method, path) for every non-static HTTP rule, one method per row."""
    for rule in app.url_map.iter_rules():
        # skip rules with URL parameters — we can't synthesise a valid one
        if "<" in rule.rule:
            continue
        # skip static asset endpoint
        if rule.endpoint == "static":
            continue
        # skip WebSocket rules — they can't be exercised via HTTP test client
        if rule.rule.startswith("/stream/"):
            continue
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            yield method, rule.rule


@pytest.fixture
def secured_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_DB_PATH", str(tmp_path / "matrix_secured.db"))
    monkeypatch.setenv("AGENTCACHE_SECRET", "matrix-secret")
    _reset_app_globals()
    return create_app()


@pytest.fixture
def open_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCACHE_DB_PATH", str(tmp_path / "matrix_open.db"))
    monkeypatch.delenv("AGENTCACHE_SECRET", raising=False)
    monkeypatch.delenv("AGENTMEMORY_SECRET", raising=False)
    _reset_app_globals()
    return create_app()


def _invoke(client, method: str, path: str, headers=None):
    fn = getattr(client, method.lower())
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return fn(path, json={}, headers=headers or {})
    return fn(path, headers=headers or {})


def test_every_route_requires_auth_when_secret_is_set(secured_app):
    """Protected routes 401 without a token; unprotected routes pass through."""
    client = secured_app.test_client()
    failures = []

    for method, path in _iter_http_rules(secured_app):
        res = _invoke(client, method, path)
        if path in UNPROTECTED_PATHS:
            if res.status_code == 401:
                failures.append(
                    f"{method} {path} unexpectedly required auth (401)"
                )
        else:
            if res.status_code != 401:
                failures.append(
                    f"{method} {path} did not require auth (got {res.status_code})"
                )

    assert not failures, "auth-matrix mismatches:\n  " + "\n  ".join(failures)


def test_every_route_accepts_valid_token(secured_app):
    """With a valid Bearer token, no route returns 401."""
    client = secured_app.test_client()
    headers = {"Authorization": "Bearer matrix-secret"}
    failures = []

    for method, path in _iter_http_rules(secured_app):
        res = _invoke(client, method, path, headers=headers)
        if res.status_code == 401:
            failures.append(f"{method} {path} rejected a valid token (401)")

    assert not failures, "rejected valid token:\n  " + "\n  ".join(failures)


def test_every_route_open_when_no_secret_configured(open_app):
    """When no secret env var is set, every route passes auth (no 401 anywhere)."""
    client = open_app.test_client()
    failures = []

    for method, path in _iter_http_rules(open_app):
        res = _invoke(client, method, path)
        if res.status_code == 401:
            failures.append(
                f"{method} {path} returned 401 in open (no-secret) mode"
            )

    assert not failures, "unexpected 401s in open mode:\n  " + "\n  ".join(failures)


def test_every_route_rejects_invalid_token(secured_app):
    """With a wrong Bearer token, protected routes must still 401."""
    client = secured_app.test_client()
    bad_headers = {"Authorization": "Bearer not-the-secret"}
    failures = []

    for method, path in _iter_http_rules(secured_app):
        if path in UNPROTECTED_PATHS:
            continue
        res = _invoke(client, method, path, headers=bad_headers)
        if res.status_code != 401:
            failures.append(
                f"{method} {path} accepted a bad token (got {res.status_code})"
            )

    assert not failures, "invalid-token acceptance:\n  " + "\n  ".join(failures)
