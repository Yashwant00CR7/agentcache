"""
Search and timeline routes blueprint.

Handles:
  POST /agentmemory/search
  POST /agentmemory/timeline
"""

import os

from flask import Blueprint, jsonify, request

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


def _get_search_service():
    from .. import app as app_module

    return app_module.search_service


def _get_observation_store():
    from .. import app as app_module

    return app_module.observation_store


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

        search_svc = _get_search_service()
        if search_svc is not None:
            res = search_svc.search(
                query=query,
                limit=limit,
                folder_path=folder_path,
                agent_id=agent_id,
                kv=_get_kv(),
            )
        else:
            res = []
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
        obs_store = _get_observation_store()
        if obs_store is not None:
            result = obs_store.timeline(
                limit=limit,
                folder_path=folder_path,
                agent_id=agent_id,
                before=before,
                after=after,
            )
        else:
            result = []
        return jsonify({"observations": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
