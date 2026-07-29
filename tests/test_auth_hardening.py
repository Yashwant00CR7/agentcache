"""Hardened authentication tests.

Motivation
----------
``tests/test_auth.py`` covers the happy-path decorator matrix (right token,
wrong token, missing header, case-insensitive header, ``AGENTMEMORY_SECRET``
fallback). What it does not exercise:

  * **Timing safety** — the docstring promises ``hmac.compare_digest``; a
    refactor to ``==`` would still pass the existing tests. This suite
    monkey-patches ``hmac.compare_digest`` and asserts it is actually
    called on every auth check.
  * **Env-var precedence** — when both ``AGENTCACHE_SECRET`` and
    ``AGENTMEMORY_SECRET`` are set, the code silently prefers the former.
    That contract is undocumented and easy to break.
  * **Malformed / hostile Authorization headers** — bare ``"Bearer"``,
    ``"Bearer "``, tabs, quoted tokens, ``:`` separator, extremely long
    tokens.
  * **WebSocket auth** — the ``/stream/mem-live/viewer`` handler runs its
    own compare_digest; a plain-HTTP-only auth suite doesn't catch a
    regression there. (We don't spin up a real WS client, but we cover the
    contract via ``verify_token`` — same function used by both.)

The tests are structured to fail with a specific, actionable message: if
the auth surface changes, you learn *what* changed.
"""

from __future__ import annotations

import hmac as std_hmac

import pytest
from flask import Flask, jsonify

from agentcache.routes import auth as auth_mod
from agentcache.routes.auth import require_auth, verify_token

# ---------------------------------------------------------------------------
# App fixture — a toy Flask app with a single protected endpoint
# ---------------------------------------------------------------------------


def _toy_app():
    app = Flask(__name__)

    @app.route("/protected")
    @require_auth
    def _p():
        return jsonify({"ok": True}), 200

    return app


@pytest.fixture
def client():
    return _toy_app().test_client()


# ---------------------------------------------------------------------------
# Constant-time comparison is actually used
# ---------------------------------------------------------------------------


def test_verify_token_uses_hmac_compare_digest(monkeypatch):
    """Locks down the timing-safe comparison. If someone refactors
    ``verify_token`` to ``==`` this fails immediately.
    """
    calls = []
    real_compare = std_hmac.compare_digest

    def _spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(auth_mod.hmac, "compare_digest", _spy)

    assert verify_token("secret", "secret") is True
    assert verify_token("nope", "secret") is False
    assert len(calls) == 2
    # Every call must pass bytes (not str) — mixing types silently returns False.
    for a, b in calls:
        assert isinstance(a, bytes) and isinstance(b, bytes), (
            "verify_token passed non-bytes to compare_digest — timing safety broken"
        )


def test_require_auth_dispatches_through_verify_token(client, monkeypatch):
    """The decorator must go through verify_token (not compare inline). This
    keeps the timing-safe check on the request path.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")

    calls = []
    real = auth_mod.verify_token

    def _spy(provided, secret):
        calls.append((provided, secret))
        return real(provided, secret)

    monkeypatch.setattr(auth_mod, "verify_token", _spy)

    client.get("/protected", headers={"Authorization": "Bearer s3cret"})
    assert calls == [("s3cret", "s3cret")]


# ---------------------------------------------------------------------------
# Env-var precedence
# ---------------------------------------------------------------------------


def test_agentcache_secret_takes_precedence_over_agentmemory_secret(client, monkeypatch):
    """When both are set, ``AGENTCACHE_SECRET`` wins. This is the current
    contract (``or`` short-circuits on the first truthy value); pinning it
    here so a refactor to the reverse order breaks loudly.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "primary-secret")
    monkeypatch.setenv("AGENTMEMORY_SECRET", "legacy-secret")

    # The legacy one must not be accepted while the primary is set.
    res = client.get("/protected", headers={"Authorization": "Bearer legacy-secret"})
    assert res.status_code == 401, (
        "AGENTMEMORY_SECRET was accepted while AGENTCACHE_SECRET was set — "
        "env-var precedence regression"
    )

    res = client.get("/protected", headers={"Authorization": "Bearer primary-secret"})
    assert res.status_code == 200


def test_agentmemory_secret_only_still_authenticates(client, monkeypatch):
    monkeypatch.delenv("AGENTCACHE_SECRET", raising=False)
    monkeypatch.setenv("AGENTMEMORY_SECRET", "fallback")

    assert client.get("/protected").status_code == 401
    assert (
        client.get(
            "/protected", headers={"Authorization": "Bearer fallback"}
        ).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# Malformed / hostile Authorization headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header_value",
    [
        "Bearer",               # scheme with no token at all
        "Bearer ",              # empty token after space
        "Bearer\t",             # whitespace-only token
        "bearer s3cret",        # wrong-case scheme (Flask docs: case-sensitive)
        "BEARER s3cret",        # upper-case scheme
        "Token s3cret",         # non-Bearer scheme with correct-looking token
        "s3cret",               # raw token, no scheme
        ":s3cret",              # colon-prefixed
        "Bearer:s3cret",        # colon separator instead of space
    ],
)
def test_malformed_authorization_header_is_rejected(client, monkeypatch, header_value):
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")
    res = client.get("/protected", headers={"Authorization": header_value})
    assert res.status_code == 401, (
        f"malformed header {header_value!r} was accepted (status "
        f"{res.status_code}) — should be 401"
    )


def test_extremely_long_token_is_rejected_not_crash(client, monkeypatch):
    """A 100kB bogus token must return a clean 401 (not 500, not OOM).
    Locks down the "no runtime cost blows up on huge inputs" contract.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")
    big = "A" * 100_000
    res = client.get("/protected", headers={"Authorization": f"Bearer {big}"})
    assert res.status_code == 401


def test_leading_and_trailing_whitespace_in_token_is_stripped(client, monkeypatch):
    """The existing test in ``test_auth.py`` observes that ``.strip()`` is
    applied; pin it as behaviour, and pin the edge cases too.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")

    ok_variants = [
        "Bearer   s3cret",       # extra leading space
        "Bearer s3cret   ",      # trailing space
        "Bearer  s3cret\t",      # tab trailing
    ]
    for header in ok_variants:
        res = client.get("/protected", headers={"Authorization": header})
        assert res.status_code == 200, (
            f"whitespace-tolerant header {header!r} unexpectedly rejected"
        )


# ---------------------------------------------------------------------------
# No-secret mode
# ---------------------------------------------------------------------------


def test_no_secret_configured_allows_any_authorization_header(client, monkeypatch):
    """Without a configured secret, ``require_auth`` short-circuits before
    it even reads the header. Locks down that a garbage header doesn't
    block open mode.
    """
    monkeypatch.delenv("AGENTCACHE_SECRET", raising=False)
    monkeypatch.delenv("AGENTMEMORY_SECRET", raising=False)

    for header in (None, "Bearer whatever", "malformed", ""):
        kw = {"headers": {"Authorization": header}} if header else {}
        res = client.get("/protected", **kw)
        assert res.status_code == 200, (
            f"open-mode rejected header {header!r} with status {res.status_code}"
        )


def test_empty_string_secret_treated_as_no_secret(client, monkeypatch):
    """``os.getenv`` returns "" for an env var set to empty string; the
    ``or`` chain skips it, so this is open mode. Locking that down.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "")
    monkeypatch.setenv("AGENTMEMORY_SECRET", "")
    res = client.get("/protected")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Cross-app end-to-end via the authed_client fixture
# ---------------------------------------------------------------------------


def test_secret_change_at_runtime_takes_effect(authed_client, monkeypatch):
    """The middleware reads the env var per-request (no cached secret).
    A rotated secret must invalidate old tokens immediately.
    """
    client, secret = authed_client
    good_headers = {"Authorization": f"Bearer {secret}"}

    # Baseline works.
    res = client.get("/agentcache/config/flags", headers=good_headers)
    assert res.status_code == 200

    # Rotate the secret; the old token must stop working.
    monkeypatch.setenv("AGENTCACHE_SECRET", "rotated-secret")

    res = client.get("/agentcache/config/flags", headers=good_headers)
    assert res.status_code == 401, (
        "old token still accepted after AGENTCACHE_SECRET rotation — "
        "middleware is caching the secret"
    )

    res = client.get(
        "/agentcache/config/flags",
        headers={"Authorization": "Bearer rotated-secret"},
    )
    assert res.status_code == 200


def test_401_body_is_json_and_carries_error_field(client, monkeypatch):
    """Contract for machine consumers: 401 responses are JSON with an
    ``error`` key, not HTML.
    """
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")
    res = client.get("/protected")
    assert res.status_code == 401
    assert res.is_json, f"401 body was not JSON: {res.get_data(as_text=True)!r}"
    body = res.get_json()
    assert body.get("error"), f"401 body missing error field: {body!r}"
