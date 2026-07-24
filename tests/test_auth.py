"""
Unit and integration tests for authentication and authorization.
"""

from flask import Flask, jsonify
from agentcache.routes.auth import require_auth, verify_token


# ------------------------------------------------------------------------------
# Unit Tests — verify_token & require_auth
# ------------------------------------------------------------------------------


def test_verify_token():
    """Verify verify_token direct behavior."""
    secret = "my-secret-key"
    assert verify_token("my-secret-key", secret) is True
    assert verify_token("wrong-secret", secret) is False
    assert verify_token("", secret) is False


def test_require_auth_unit(monkeypatch):
    """Test require_auth decorator behavior under various header & env combinations on a toy app."""
    app = Flask(__name__)

    @app.route("/protected")
    @require_auth
    def protected():
        return jsonify({"status": "ok"}), 200

    client = app.test_client()

    # 1. No secret set -> request passes through with 200
    monkeypatch.delenv("AGENTCACHE_SECRET", raising=False)
    monkeypatch.delenv("AGENTMEMORY_SECRET", raising=False)
    res = client.get("/protected")
    assert res.status_code == 200

    # Set secret for remaining tests
    monkeypatch.setenv("AGENTCACHE_SECRET", "secret-token")

    # 2. Missing Authorization header -> 401
    res = client.get("/protected")
    assert res.status_code == 401

    # 3. Basic scheme (non-Bearer) -> 401
    res = client.get("/protected", headers={"Authorization": "Basic secret-token"})
    assert res.status_code == 401

    # 4. Correct Bearer token -> 200
    res = client.get("/protected", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200

    # 5. Wrong Bearer token -> 401
    res = client.get("/protected", headers={"Authorization": "Bearer wrong-token"})
    assert res.status_code == 401

    # 6. Lowercase authorization header -> 200
    res = client.get("/protected", headers={"authorization": "Bearer secret-token"})
    assert res.status_code == 200

    # 7. Token with leading/trailing whitespace -> 200 (.strip() confirmed)
    res = client.get(
        "/protected", headers={"Authorization": "Bearer   secret-token   "}
    )
    assert res.status_code == 200

    # 8. Secret set via AGENTMEMORY_SECRET fallback
    monkeypatch.delenv("AGENTCACHE_SECRET")
    monkeypatch.setenv("AGENTMEMORY_SECRET", "fallback-secret")
    res = client.get("/protected", headers={"Authorization": "Bearer fallback-secret"})
    assert res.status_code == 200
    res = client.get("/protected", headers={"Authorization": "Bearer wrong-secret"})
    assert res.status_code == 401


# ------------------------------------------------------------------------------
# Integration Tests — protected & unprotected routes via authed_client
# ------------------------------------------------------------------------------


def test_protected_routes_require_auth(authed_client):
    """Verify all protected HTTP routes return 401 without auth and 200/201 with auth."""
    client, secret = authed_client
    headers = {"Authorization": f"Bearer {secret}"}

    protected_endpoints = [
        ("POST", "/agentcache/observe", {"folderPath": "src/test", "agentId": "a1", "text": "test"}),
        ("POST", "/agentcache/remember", {"content": "test memory"}),
        ("POST", "/agentcache/search", {"query": "test"}),
        ("POST", "/agentcache/timeline", {}),
        ("GET", "/agentcache/graph", None),
        ("GET", "/agentcache/audit", None),
        ("GET", "/agentcache/config/flags", None),
        ("POST", "/agentcache/migrate", {}),
    ]

    for method, path, payload in protected_endpoints:
        # Unauthenticated request -> 401
        if method == "POST":
            res_unauth = client.post(path, json=payload or {})
            res_auth = client.post(path, json=payload or {}, headers=headers)
        else:
            res_unauth = client.get(path)
            res_auth = client.get(path, headers=headers)

        assert res_unauth.status_code == 401, f"{method} {path} should require auth (got {res_unauth.status_code})"
        assert res_auth.status_code in (200, 201), f"{method} {path} failed with valid auth (got {res_auth.status_code})"


def test_unprotected_routes_accessible_without_auth(authed_client):
    """Verify unprotected routes return 200 even when AGENTCACHE_SECRET is set and no token is provided."""
    client, _ = authed_client

    unprotected_paths = [
        "/agentcache/livez",
        "/agentcache/health",
        "/auth.md",
    ]

    for path in unprotected_paths:
        res = client.get(path)
        assert res.status_code == 200, f"Unprotected route {path} failed (got {res.status_code})"


def test_wrong_token_on_any_blueprint_returns_401(authed_client):
    """Verify an invalid token returns 401 across all protected blueprints."""
    client, _ = authed_client
    bad_headers = {"Authorization": "Bearer wrong-token-value"}

    protected_endpoints = [
        ("POST", "/agentcache/observe", {"folderPath": "src/test", "agentId": "a1", "text": "test"}),
        ("POST", "/agentcache/remember", {"content": "test memory"}),
        ("POST", "/agentcache/search", {"query": "test"}),
        ("POST", "/agentcache/timeline", {}),
        ("GET", "/agentcache/graph", None),
        ("GET", "/agentcache/audit", None),
        ("GET", "/agentcache/config/flags", None),
        ("POST", "/agentcache/migrate", {}),
    ]


    for method, path, payload in protected_endpoints:
        if method == "POST":
            res = client.post(path, json=payload or {}, headers=bad_headers)
        else:
            res = client.get(path, headers=bad_headers)

        assert res.status_code == 401, f"{method} {path} accepted invalid token!"
