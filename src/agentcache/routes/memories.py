"""
Memory routes blueprint.

Handles:
  POST /agentmemory/remember
  POST /agentmemory/agent/remember
  GET  /agentmemory/memories
  POST /agentmemory/forget
"""

from flask import Blueprint, jsonify, request

from .. import legacy as functions
from ..core import KV
from .auth import require_auth


def create_memories_bp(kv=None):
    """Blueprint factory — receives kv at registration time."""
    bp = Blueprint("memories", __name__)

    def _get_kv():
        if kv is not None:
            return kv
        from .. import app as app_module

        return app_module.kv

    # ------------------------------------------------------------------
    # POST /agentmemory/remember
    # ------------------------------------------------------------------

    @bp.route("/agentcache/remember", methods=["POST"])
    @bp.route("/agentmemory/remember", methods=["POST"])
    @require_auth
    def api_remember():
        try:
            body = request.get_json(force=True) or {}
            res = functions.remember(_get_kv(), body)
            return jsonify(res), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    # ------------------------------------------------------------------
    # POST /agentmemory/agent/remember
    # ------------------------------------------------------------------

    @bp.route("/agentcache/agent/remember", methods=["POST"])
    @bp.route("/agentmemory/agent/remember", methods=["POST"])
    @require_auth
    def api_agent_remember():
        try:
            body = request.get_json(force=True) or {}
            content = body.get("content")
            if not content:
                return jsonify({"error": "content is required"}), 400

            agent_id = body.get("agentId")
            project = body.get("project")
            mem_type = body.get("type") or "fact"

            concepts = body.get("concepts") or []
            if isinstance(concepts, str):
                concepts = [c.strip() for c in concepts.split(",") if c.strip()]

            files = body.get("files") or []
            if isinstance(files, str):
                files = [f.strip() for f in files.split(",") if f.strip()]

            payload = {
                "content": content,
                "type": mem_type,
                "concepts": concepts,
                "files": files,
                "project": project,
            }
            if agent_id:
                payload["agentId"] = agent_id

            res = functions.remember(_get_kv(), payload)
            return jsonify(res), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    # ------------------------------------------------------------------
    # GET /agentmemory/memories
    # ------------------------------------------------------------------

    @bp.route("/agentcache/memories", methods=["GET"])
    @bp.route("/agentmemory/memories", methods=["GET"])
    @require_auth
    def api_memories_list():
        latest_only = request.args.get("latest", "false").lower() == "true"
        limit = int(request.args.get("limit", "500"))
        all_mems = _get_kv().list(KV.memories)
        if latest_only:
            all_mems = [m for m in all_mems if m.get("isLatest") is not False]
        all_mems.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
        return jsonify({"memories": all_mems[:limit], "total": len(all_mems)}), 200

    # ------------------------------------------------------------------
    # POST /agentmemory/forget
    # ------------------------------------------------------------------

    @bp.route("/agentcache/forget", methods=["POST"])
    @bp.route("/agentmemory/forget", methods=["POST"])
    @require_auth
    def api_forget():
        try:
            body = request.get_json(force=True) or {}
            res = functions.forget(_get_kv(), body)
            return jsonify(res), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return bp


memories_bp = create_memories_bp(None)
