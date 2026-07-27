"""
Session pipeline routes blueprint (#41).

Rebuilds the PreCompact / Stop / SessionEnd hook endpoints on top of the
folder-scoped memory model. Every route accepts the hook-friendly
``{sessionId, project, cwd}`` shape and maps it to a (folderPath, agentId)
scope inside the ported core functions.

Handles:
  POST /agentmemory/context               (#45)
  POST /agentmemory/summarize             (#46)
  POST /agentmemory/consolidate-pipeline  (#47)
  POST /agentmemory/crystals/auto         (#47)
"""

from flask import Blueprint, jsonify, request

from .. import legacy as functions
from ._deps import get_kv
from .auth import require_auth


def create_pipeline_bp(kv=None):
    """Blueprint factory — receives kv at registration time (falls back to get_kv())."""
    bp = Blueprint("pipeline", __name__)

    def _kv():
        return kv if kv is not None else get_kv()

    @bp.route("/agentcache/context", methods=["POST"])
    @bp.route("/agentmemory/context", methods=["POST"])
    @require_auth
    def api_context():
        try:
            body = request.get_json(force=True) or {}
            result = functions.context(_kv(), body)
            return jsonify(result), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @bp.route("/agentcache/summarize", methods=["POST"])
    @bp.route("/agentmemory/summarize", methods=["POST"])
    @require_auth
    def api_summarize():
        try:
            body = request.get_json(force=True) or {}
            result = functions.summarize(_kv(), body)
            status = 200 if result.get("success") else 400
            return jsonify(result), status
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400

    def _run_consolidation():
        try:
            if not functions.is_consolidation_enabled():
                return (
                    jsonify({"success": True, "skipped": "consolidation_disabled"}),
                    200,
                )
            body = request.get_json(force=True, silent=True) or {}
            result = functions.consolidate(_kv(), body)
            return jsonify(result), 200
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400

    @bp.route("/agentcache/consolidate-pipeline", methods=["POST"])
    @bp.route("/agentmemory/consolidate-pipeline", methods=["POST"])
    @require_auth
    def api_consolidate_pipeline():
        return _run_consolidation()

    @bp.route("/agentcache/crystals/auto", methods=["POST"])
    @bp.route("/agentmemory/crystals/auto", methods=["POST"])
    @require_auth
    def api_crystals_auto():
        return _run_consolidation()

    return bp


pipeline_bp = create_pipeline_bp(None)
