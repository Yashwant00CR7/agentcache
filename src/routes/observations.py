"""
Observation routes blueprint.

Handles:
  POST /agentmemory/observe
  POST /agentmemory/agent/observe
  GET  /agentmemory/folder/observations
  GET  /agentmemory/folders
"""

import os
import datetime
from flask import Blueprint, request, jsonify
import functions
from functions import KV

observations_bp = Blueprint("observations", __name__)


def _datetime_now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _check_auth():
    """Replicate the check_auth() pattern from app.py."""
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
    """Retrieve the shared kv instance from the app module."""
    import app as app_module
    return app_module.kv


# ---------------------------------------------------------------------------
# POST /agentmemory/observe  (legacy raw hook endpoint + auto-compat shim)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/observe", methods=["POST"])
def api_observe():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}

        # Auto-detect folder-based payload: if folderPath + agentId present,
        # route to folder_observe instead of legacy observe().
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")
        text = body.get("text") or body.get("content") or ""

        if folder_path and agent_id:
            # New folder-based model — delegate to folder_observe
            payload = {
                "folderPath": folder_path,
                "agentId": agent_id,
                "text": text,
                "timestamp": body.get("timestamp") or _datetime_now_iso(),
                "type": body.get("type"),
                "title": body.get("title"),
                "concepts": body.get("concepts"),
                "files": body.get("files"),
                "importance": body.get("importance"),
            }
            res = functions.folder_observe(_get_kv(), payload)
            return jsonify(res), 201

        # Legacy session-based model
        res = functions.observe(_get_kv(), body)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# POST /agentmemory/agent/observe
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/agent/observe", methods=["POST"])
def api_agent_observe():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        body = request.get_json(force=True) or {}
        folder_path = body.get("folderPath")
        agent_id = body.get("agentId")
        text = body.get("text") or body.get("content") or ""

        if not folder_path or not agent_id or not text:
            return jsonify({"error": "folderPath, agentId, and text are required"}), 400

        # sessionId accepted but ignored (folder-based model)
        timestamp = body.get("timestamp") or _datetime_now_iso()

        payload = {
            "folderPath": folder_path,
            "agentId": agent_id,
            "text": text,
            "timestamp": timestamp,
            "type": body.get("type"),
            "title": body.get("title"),
            "concepts": body.get("concepts"),
            "files": body.get("files"),
            "importance": body.get("importance"),
        }

        res = functions.folder_observe(_get_kv(), payload)
        return jsonify(res), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# GET /agentmemory/folders
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/folders", methods=["GET"])
def api_folders():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    folders = sorted(
        _get_kv().list(KV.folders),
        key=lambda x: x.get("lastUpdated", ""),
        reverse=True,
    )
    return jsonify({"folders": folders}), 200


# ---------------------------------------------------------------------------
# GET /agentmemory/folder/observations
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/folder/observations", methods=["GET"])
def api_folder_observations():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    fp = request.args.get("folderPath")
    aid = request.args.get("agentId")
    if not fp or not aid:
        return jsonify({"error": "folderPath and agentId are required"}), 400
    observations = sorted(
        _get_kv().list(KV.folder_obs(fp, aid)),
        key=lambda x: x.get("timestamp", ""),
        reverse=True,
    )
    return jsonify({"observations": observations, "folderPath": fp, "agentId": aid}), 200


# ---------------------------------------------------------------------------
# POST /agentmemory/session/start  (legacy compat shim → 200 no-op)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/session/start", methods=["POST"])
def api_session_start():
    """Legacy session/start — clients in the wild still call this.
    Return a synthetic session ID so callers don't error out.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    import uuid
    body = request.get_json(force=True) or {}
    session_id = body.get("sessionId") or f"compat_{uuid.uuid4().hex[:16]}"
    return jsonify({
        "sessionId": session_id,
        "status": "active",
        "message": "Session model migrated to folder-based. Use /agentmemory/agent/observe.",
    }), 200


# ---------------------------------------------------------------------------
# POST /agentmemory/session/end  (legacy compat shim → 200 no-op)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/session/end", methods=["POST"])
def api_session_end():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    return jsonify({"success": True, "message": "Session model is now folder-based."}), 200


# ---------------------------------------------------------------------------
# GET /agentmemory/observations  (legacy compat shim)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentmemory/observations", methods=["GET"])
def api_observations_legacy():
    """Legacy /observations?sessionId=... shim.
    Reads from legacy KV scope if data exists, otherwise returns empty list.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    session_id = request.args.get("sessionId", "")
    if not session_id:
        return jsonify({"observations": [], "sessionId": ""}), 200

    try:
        obs = sorted(
            _get_kv().list(functions.KV.observations(session_id)),
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )
        return jsonify({"observations": obs, "sessionId": session_id}), 200
    except Exception as e:
        return jsonify({"observations": [], "sessionId": session_id, "error": str(e)}), 200
