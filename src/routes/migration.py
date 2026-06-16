"""
Migration route blueprint.

Handles:
  POST /agentmemory/migrate
"""

import os
from flask import Blueprint, request, jsonify
import functions

migration_bp = Blueprint("migration", __name__)


def _check_auth():
    import hmac
    secret = os.getenv("AGENTCACHE_SECRET") or os.getenv("AGENTMEMORY_SECRET")
    if not secret:
        return None
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"error": "unauthorized"}), 401
    provided_token = auth[7:].strip()
    if not hmac.compare_digest(provided_token.encode("utf-8"), secret.encode("utf-8")):
        return jsonify({"error": "unauthorized"}), 401
    return None


def _get_kv():
    import app as app_module
    return app_module.kv


# ---------------------------------------------------------------------------
# POST /agentcache/migrate
# ---------------------------------------------------------------------------

@migration_bp.route("/agentcache/migrate", methods=["POST"])
@migration_bp.route("/agentmemory/migrate", methods=["POST"])
def api_migrate():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        dry_run = bool(body.get("dry_run", False))
        result = functions.migrate_sessions_to_folders(_get_kv(), dry_run)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
