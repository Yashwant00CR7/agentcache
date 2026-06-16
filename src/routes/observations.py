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
    """Retrieve the shared kv instance from the app module."""
    import app as app_module
    return app_module.kv


# ---------------------------------------------------------------------------
# POST /agentmemory/observe  (legacy raw hook endpoint + auto-compat shim)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/observe", methods=["POST"])
@observations_bp.route("/agentmemory/observe", methods=["POST"])
def api_observe():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    body = {}
    try:
        body = request.get_json(force=True) or {}

        # Compat shim: accept both folder-based and legacy session-based payloads.
        # Old clients send sessionId/project/cwd + data{tool_input/output}
        # New clients send folderPath/agentId/text
        folder_path = (
            body.get("folderPath")
            or body.get("cwd")
            or body.get("project")
            or os.getenv("AGENTCACHE_CWD")
            or os.getenv("AGENTMEMORY_CWD")
            or "/unknown"
        )
        agent_id = (
            body.get("agentId")
            or body.get("sessionId")
            or functions.get_agent_id()
            or os.getenv("AGENT_ID")
            or "agent"
        )

        # Build text from whichever field the client used
        text = body.get("text") or body.get("content") or ""
        if not text:
            # Legacy clients put content in a nested 'data' dict
            data = body.get("data")
            if isinstance(data, dict):
                parts = [str(v) for k, v in data.items()
                         if v and k in ("tool_input", "tool_output", "prompt",
                                        "response", "tool_name", "content")]
                text = " | ".join(parts) if parts else str(data)
            elif isinstance(data, str):
                text = data
        if not text:
            # Last resort: use hookType as a minimal marker so we don't 400
            text = body.get("hookType") or "observation"

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
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[observe] 400 — keys={list(body.keys())} {type(e).__name__}: {e}\n{tb}")
        return jsonify({"error": str(e), "detail": type(e).__name__, "keys": list(body.keys()), "tb": tb}), 400


# ---------------------------------------------------------------------------
# POST /agentmemory/agent/observe
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/agent/observe", methods=["POST"])
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
        import traceback
        print(f"[agent_observe] 400 ValueError — body keys: {list(body.keys())} — {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        print(f"[agent_observe] 400 error — body keys: {list(body.keys())} — {type(e).__name__}: {e}")
        print(traceback.format_exc())
        return jsonify({"error": str(e), "detail": type(e).__name__}), 400


# ---------------------------------------------------------------------------
# GET /agentmemory/folders
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/folders", methods=["GET"])
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
    if functions.is_agent_scope_isolated():
        aid = functions.get_agent_id()
        if aid:
            folders = [f for f in folders if f.get("agentId") == aid]
    return jsonify({"folders": folders}), 200


# ---------------------------------------------------------------------------
# GET /agentmemory/folder/observations
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/folder/observations", methods=["GET"])
@observations_bp.route("/agentmemory/folder/observations", methods=["GET"])
def api_folder_observations():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    fp = request.args.get("folderPath")
    aid = request.args.get("agentId")
    if not fp or not aid:
        return jsonify({"error": "folderPath and agentId are required"}), 400
    if functions.is_agent_scope_isolated():
        current_aid = functions.get_agent_id()
        if current_aid and aid != current_aid:
            return jsonify({"error": "Unauthorized: Agent scope is isolated to another agent"}), 403
    observations = sorted(
        _get_kv().list(KV.folder_obs(fp, aid)),
        key=lambda x: x.get("timestamp", ""),
        reverse=True,
    )
    return jsonify({"observations": observations, "folderPath": fp, "agentId": aid}), 200


# ---------------------------------------------------------------------------
# POST /agentmemory/session/start  (legacy compat shim → 200 no-op)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/session/start", methods=["POST"])
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

@observations_bp.route("/agentcache/session/end", methods=["POST"])
@observations_bp.route("/agentmemory/session/end", methods=["POST"])
def api_session_end():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    return jsonify({"success": True, "message": "Session model is now folder-based."}), 200


# ---------------------------------------------------------------------------
# GET /agentmemory/observations  (legacy compat shim)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/folder/dedup", methods=["POST"])
@observations_bp.route("/agentmemory/folder/dedup", methods=["POST"])
def api_folder_dedup():
    """POST /agentmemory/folder/dedup — remove duplicate observations.

    Body (both optional):
        folderPath: str  — deduplicate only this folder pair
        agentId:    str  — deduplicate only this agent

    If both are omitted all folder pairs are processed.
    Returns: {"success": bool, "deduplicated": int, "pairs_processed": int, "kept": int}
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        folder_path = body.get("folderPath") or None
        agent_id = body.get("agentId") or None
        res = functions.dedup_folder_observations(_get_kv(), folder_path, agent_id)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# GET /agentmemory/observations  (legacy compat shim)
# ---------------------------------------------------------------------------

@observations_bp.route("/agentcache/observations", methods=["GET"])
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
