"""
Search and timeline routes blueprint.

Handles:
  POST /agentmemory/search
  POST /agentmemory/timeline
"""

import os

from flask import Blueprint, jsonify, request

from .. import functions

search_bp = Blueprint("search", __name__)


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
    from .. import app as app_module

    return app_module.kv


# ---------------------------------------------------------------------------
# POST /agentcache/search
# ---------------------------------------------------------------------------


@search_bp.route("/agentcache/search", methods=["POST"])
@search_bp.route("/agentmemory/search", methods=["POST"])
def api_search():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}
        query = body.get("query")
        if not query or not query.strip():
            return jsonify({"error": "query is required"}), 400
        limit = body.get("limit") or 10
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")

        res = functions.folder_search(
            _get_kv(), query, limit, folder_path=folder_path, agent_id=agent_id
        )
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# POST /agentcache/timeline
# ---------------------------------------------------------------------------


@search_bp.route("/agentcache/timeline", methods=["POST"])
@search_bp.route("/agentmemory/timeline", methods=["POST"])
def api_timeline():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")
        limit = body.get("limit") or 100
        before = body.get("before")
        after = body.get("after")
        result = functions.folder_timeline(
            _get_kv(), limit, folder_path, agent_id, before, after
        )
        return jsonify({"observations": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
