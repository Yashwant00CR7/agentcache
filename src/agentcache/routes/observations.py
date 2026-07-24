"""
Observation routes blueprint.

Handles:
  POST /agentcache/observe
  POST /agentcache/agent/observe
  GET  /agentcache/folder/observations
  GET  /agentcache/folders
  POST /agentcache/folder/dedup
"""

import datetime
import os
from typing import Optional

from flask import Blueprint, jsonify, request

from ..core.kv_scopes import KV
from ..core.observation_store import ObservationStore


def _datetime_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )


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


def create_observations_bp(
    observation_store: Optional[ObservationStore] = None,
) -> Blueprint:
    bp = Blueprint("observations", __name__)

    def get_store() -> ObservationStore:
        if observation_store is not None:
            return observation_store
        from flask import current_app

        store = current_app.extensions.get("observation_store")
        if store is None:
            from .. import app as app_module

            store = getattr(app_module, "observation_store", None)
        if store is None:
            raise RuntimeError("ObservationStore is not initialized")
        return store

    def get_kv():
        return get_store().kv

    @bp.route("/agentcache/observe", methods=["POST"])
    @bp.route("/agentmemory/observe", methods=["POST"])
    def api_observe():
        auth_err = _check_auth()
        if auth_err:
            return auth_err

        body = {}
        try:
            body = request.get_json(force=True) or {}
            folder_path = body.get("folderPath")
            agent_id = body.get("agentId")
            text = body.get("text") or body.get("content") or ""

            if not folder_path or not agent_id or not text:
                return (
                    jsonify({"error": "folderPath, agentId, and text are required"}),
                    400,
                )

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
            res = get_store().ingest(payload)
            return jsonify(res), 201
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            print(
                f"[observe] 400 — keys={list(body.keys())} {type(e).__name__}: {e}\n{tb}"
            )
            return (
                jsonify(
                    {
                        "error": str(e),
                        "detail": type(e).__name__,
                        "keys": list(body.keys()),
                        "tb": tb,
                    }
                ),
                400,
            )

    @bp.route("/agentcache/agent/observe", methods=["POST"])
    @bp.route("/agentmemory/agent/observe", methods=["POST"])
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
                return (
                    jsonify({"error": "folderPath, agentId, and text are required"}),
                    400,
                )

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

            res = get_store().ingest(payload)
            return jsonify(res), 201
        except ValueError as e:
            print(
                f"[agent_observe] 400 ValueError — body keys: {list(body.keys())} — {e}"
            )
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            import traceback

            print(
                f"[agent_observe] 400 error — body keys: {list(body.keys())} — {type(e).__name__}: {e}"
            )
            print(traceback.format_exc())
            return jsonify({"error": str(e), "detail": type(e).__name__}), 400

    @bp.route("/agentcache/folders", methods=["GET"])
    @bp.route("/agentmemory/folders", methods=["GET"])
    def api_folders():
        auth_err = _check_auth()
        if auth_err:
            return auth_err
        from .. import legacy

        folders = sorted(
            get_kv().list(KV.folders),
            key=lambda x: x.get("lastUpdated", ""),
            reverse=True,
        )
        if legacy.is_agent_scope_isolated():
            aid = legacy.get_agent_id()
            if aid:
                folders = [f for f in folders if f.get("agentId") == aid]
        return jsonify({"folders": folders}), 200

    @bp.route("/agentcache/folder/observations", methods=["GET"])
    @bp.route("/agentmemory/folder/observations", methods=["GET"])
    def api_folder_observations():
        auth_err = _check_auth()
        if auth_err:
            return auth_err
        fp = request.args.get("folderPath")
        aid = request.args.get("agentId")
        if not fp or not aid:
            return jsonify({"error": "folderPath and agentId are required"}), 400
        from .. import legacy

        if legacy.is_agent_scope_isolated():
            current_aid = legacy.get_agent_id()

            if current_aid and aid != current_aid:
                return (
                    jsonify(
                        {
                            "error": "Unauthorized: Agent scope is isolated to another agent"
                        }
                    ),
                    403,
                )
        observations = sorted(
            get_kv().list(KV.folder_obs(fp, aid)),
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )
        return (
            jsonify({"observations": observations, "folderPath": fp, "agentId": aid}),
            200,
        )

    @bp.route("/agentcache/session/start", methods=["POST"])
    @bp.route("/agentmemory/session/start", methods=["POST"])
    def api_session_start():
        auth_err = _check_auth()
        if auth_err:
            return auth_err

        import uuid

        body = request.get_json(force=True) or {}
        session_id = body.get("sessionId") or f"compat_{uuid.uuid4().hex[:16]}"
        return (
            jsonify(
                {
                    "sessionId": session_id,
                    "status": "active",
                    "message": "Session model migrated to folder-based. Use /agentmemory/agent/observe.",
                }
            ),
            200,
        )

    @bp.route("/agentcache/session/end", methods=["POST"])
    @bp.route("/agentmemory/session/end", methods=["POST"])
    def api_session_end():
        auth_err = _check_auth()
        if auth_err:
            return auth_err
        return (
            jsonify({"success": True, "message": "Session model is now folder-based."}),
            200,
        )

    @bp.route("/agentcache/folder/dedup", methods=["POST"])
    @bp.route("/agentmemory/folder/dedup", methods=["POST"])
    def api_folder_dedup():
        auth_err = _check_auth()
        if auth_err:
            return auth_err
        try:
            body = request.get_json(force=True) or {}
            folder_path = body.get("folderPath") or None
            agent_id = body.get("agentId") or None
            res = get_store().dedup(folder_path, agent_id)
            return jsonify(res), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @bp.route("/agentcache/observations", methods=["GET"])
    @bp.route("/agentmemory/observations", methods=["GET"])
    def api_observations_legacy():
        auth_err = _check_auth()
        if auth_err:
            return auth_err

        session_id = request.args.get("sessionId", "")
        if not session_id:
            return jsonify({"observations": [], "sessionId": ""}), 200

        try:
            obs = sorted(
                get_kv().list(KV.observations(session_id)),
                key=lambda x: x.get("timestamp", ""),
                reverse=True,
            )
            return jsonify({"observations": obs, "sessionId": session_id}), 200
        except Exception as e:
            return (
                jsonify({"observations": [], "sessionId": session_id, "error": str(e)}),
                200,
            )

    return bp


observations_bp = create_observations_bp()
