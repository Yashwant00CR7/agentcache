"""
Shared authentication utilities for all route blueprints.

Public API
----------
verify_token(provided, secret) -> bool
    Raw HMAC comparison. Used directly by the WebSocket handler in app.py.

require_auth
    Flask route decorator. Reads AGENTCACHE_SECRET / AGENTMEMORY_SECRET from
    the environment, validates the Authorization: Bearer <token> header, and
    aborts with 401 JSON if the check fails. Routes with no secret configured
    are always allowed through.

Usage in a blueprint
--------------------
    from .auth import require_auth

    @bp.route("/agentcache/something", methods=["GET"])
    @require_auth
    def api_something():
        ...
"""

import hmac
import os
from functools import wraps

from flask import abort, jsonify, make_response, request


def verify_token(provided: str, secret: str) -> bool:
    """Return True if *provided* matches *secret* via constant-time comparison."""
    return hmac.compare_digest(
        provided.encode("utf-8"),
        secret.encode("utf-8"),
    )


def require_auth(f):
    """Decorator that enforces Bearer-token authentication on a Flask route.

    If no secret is configured the request is passed through unchanged —
    consistent with the previous per-blueprint behaviour.
    """

    @wraps(f)
    def _wrapped(*args, **kwargs):
        secret = os.getenv("AGENTCACHE_SECRET") or os.getenv("AGENTMEMORY_SECRET")
        if not secret:
            # No secret configured → open access (matches legacy behaviour).
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization") or request.headers.get(
            "authorization"
        )
        if not auth_header or not auth_header.startswith("Bearer "):
            resp = make_response(jsonify({"error": "unauthorized"}), 401)
            abort(resp)

        provided_token = auth_header[7:].strip()
        if not verify_token(provided_token, secret):
            resp = make_response(jsonify({"error": "unauthorized"}), 401)
            abort(resp)

        return f(*args, **kwargs)

    return _wrapped
