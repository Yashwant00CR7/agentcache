"""
Memory routes blueprint.

Handles:
  POST /agentmemory/remember
  POST /agentmemory/agent/remember
  GET  /agentmemory/memories
  POST /agentmemory/forget
"""

import os
from flask import Blueprint, request, jsonify
import functions
from functions import KV

memories_bp = Blueprint("memories", __name__)


def _check_auth():
    import hmac
    secret = os.getenv("AGENTMEMORY_SECRET")
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
# POST /agentmemory/remember
# ---------------------------------------------------------------------------

@memories_bp.route("/agentmemory/remember", methods=["POST"])
def api_remember():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}
        res = functions.remember(_get_kv(), body)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# POST /agentmemory/agent/remember
# ---------------------------------------------------------------------------

@memories_bp.route("/agentmemory/agent/remember", methods=["POST"])
def api_agent_remember():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

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


# ---------------------------------------------------------------------------
# GET /agentmemory/memories
# ---------------------------------------------------------------------------

@memories_bp.route("/agentmemory/memories", methods=["GET"])
def api_memories_list():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    latest_only = request.args.get("latest", "false").lower() == "true"
    limit = int(request.args.get("limit", "500"))
    all_mems = _get_kv().list(KV.memories)
    if latest_only:
        all_mems = [m for m in all_mems if m.get("isLatest") is not False]
    all_mems.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    return jsonify({"memories": all_mems[:limit], "total": len(all_mems)}), 200


# ---------------------------------------------------------------------------
# POST /agentmemory/forget
# ---------------------------------------------------------------------------

@memories_bp.route("/agentmemory/forget", methods=["POST"])
def api_forget():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}
        res = functions.forget(_get_kv(), body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
