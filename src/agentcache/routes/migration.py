"""
Migration route blueprint.

Handles:
  POST /agentmemory/migrate
"""

from flask import Blueprint, jsonify, request

from .. import legacy as functions
from ._deps import get_kv
from .auth import require_auth

migration_bp = Blueprint("migration", __name__)


# ---------------------------------------------------------------------------
# POST /agentcache/migrate
# ---------------------------------------------------------------------------


@migration_bp.route("/agentcache/migrate", methods=["POST"])
@migration_bp.route("/agentmemory/migrate", methods=["POST"])
@require_auth
def api_migrate():
    try:
        body = request.get_json(force=True) or {}
        dry_run = bool(body.get("dry_run", False))
        result = functions.migrate_sessions_to_folders(get_kv(), dry_run)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
