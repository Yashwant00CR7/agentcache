"""
Search and timeline routes blueprint.

Handles:
  POST /agentmemory/search
  POST /agentmemory/timeline
"""

from flask import Blueprint, jsonify, request

from ._deps import get_kv, get_observation_store, get_search_service
from .auth import require_auth

search_bp = Blueprint("search", __name__)


# ---------------------------------------------------------------------------
# POST /agentcache/search
# ---------------------------------------------------------------------------


@search_bp.route("/agentcache/search", methods=["POST"])
@search_bp.route("/agentmemory/search", methods=["POST"])
@require_auth
def api_search():
    try:
        body = request.get_json(force=True) or {}
        query = body.get("query")
        if not query or not query.strip():
            return jsonify({"error": "query is required"}), 400
        limit = body.get("limit") or 10
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")

        search_svc = get_search_service()
        if search_svc is not None:
            res = search_svc.search(
                query=query,
                limit=limit,
                folder_path=folder_path,
                agent_id=agent_id,
                kv=get_kv(),
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
@require_auth
def api_timeline():
    try:
        body = request.get_json(force=True) or {}
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")
        limit = body.get("limit") or 100
        before = body.get("before")
        after = body.get("after")
        obs_store = get_observation_store()
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
