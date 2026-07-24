"""
Graph routes blueprint.

Handles:
  GET  /agentmemory/graph
  GET  /agentmemory/graph/stats
  POST /agentmemory/graph/query
  POST /agentmemory/graph/build
"""

from flask import Blueprint, jsonify, request

from .. import legacy as functions
from .auth import require_auth


def create_graph_bp(kv=None):
    """Blueprint factory — receives kv at registration time."""
    bp = Blueprint("graph", __name__)

    def _get_kv():
        if kv is not None:
            return kv
        from .. import app as app_module
        return app_module.kv

    # ------------------------------------------------------------------
    # GET /agentcache/graph
    # ------------------------------------------------------------------

    @bp.route("/agentcache/graph", methods=["GET"])
    @bp.route("/agentmemory/graph", methods=["GET"])
    @require_auth
    def api_graph():
        result = functions.folder_graph_build(_get_kv())
        return jsonify(result), 200

    # ------------------------------------------------------------------
    # GET /agentcache/graph/stats
    # ------------------------------------------------------------------

    @bp.route("/agentcache/graph/stats", methods=["GET"])
    @bp.route("/agentmemory/graph/stats", methods=["GET"])
    @require_auth
    def api_graph_stats():
        g = functions.folder_graph_build(_get_kv())
        node_count = len(g.get("nodes", []))
        edge_count = len(g.get("edges", []))
        return jsonify({"nodes": node_count, "edges": edge_count, "success": True}), 200

    # ------------------------------------------------------------------
    # POST /agentcache/graph/query
    # ------------------------------------------------------------------

    @bp.route("/agentcache/graph/query", methods=["POST"])
    @bp.route("/agentmemory/graph/query", methods=["POST"])
    @require_auth
    def api_graph_query():
        try:
            request.get_json(force=True) or {}
            # start_node_id = body.get("startNodeId")  # reserved for future use
            return jsonify({"nodes": [], "edges": [], "success": True}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    # ------------------------------------------------------------------
    # POST /agentcache/graph/build
    # ------------------------------------------------------------------

    @bp.route("/agentcache/graph/build", methods=["POST"])
    @bp.route("/agentmemory/graph/build", methods=["POST"])
    @require_auth
    def api_graph_build():
        try:
            if functions.is_consolidation_enabled():
                functions.consolidate(_get_kv())
            return jsonify({"success": True}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return bp



graph_bp = create_graph_bp(None)
