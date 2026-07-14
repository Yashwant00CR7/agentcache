"""
Graph routes blueprint.

Handles:
  GET  /agentmemory/graph
  GET  /agentmemory/graph/stats
  POST /agentmemory/graph/query
  POST /agentmemory/graph/build
"""

import os
from flask import Blueprint, request, jsonify
import functions

graph_bp = Blueprint("graph", __name__)


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
    import app as app_module

    return app_module.kv


# ---------------------------------------------------------------------------
# GET /agentcache/graph
# ---------------------------------------------------------------------------


@graph_bp.route("/agentcache/graph", methods=["GET"])
@graph_bp.route("/agentmemory/graph", methods=["GET"])
def api_graph():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    result = functions.folder_graph_build(_get_kv())
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# GET /agentcache/graph/stats
# ---------------------------------------------------------------------------


@graph_bp.route("/agentcache/graph/stats", methods=["GET"])
@graph_bp.route("/agentmemory/graph/stats", methods=["GET"])
def api_graph_stats():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    kv = _get_kv()
    g = functions.folder_graph_build(kv)
    node_count = len(g.get("nodes", []))
    edge_count = len(g.get("edges", []))

    return jsonify({"nodes": node_count, "edges": edge_count, "success": True}), 200


# ---------------------------------------------------------------------------
# POST /agentcache/graph/query
# ---------------------------------------------------------------------------


@graph_bp.route("/agentcache/graph/query", methods=["POST"])
@graph_bp.route("/agentmemory/graph/query", methods=["POST"])
def api_graph_query():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    try:
        request.get_json(force=True) or {}
        # start_node_id = body.get("startNodeId")  # reserved for future use
        return jsonify({"nodes": [], "edges": [], "success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# POST /agentcache/graph/build
# ---------------------------------------------------------------------------


@graph_bp.route("/agentcache/graph/build", methods=["POST"])
@graph_bp.route("/agentmemory/graph/build", methods=["POST"])
def api_graph_build():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    try:
        if functions.is_consolidation_enabled():
            functions.consolidate(_get_kv())
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
