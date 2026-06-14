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
from functions import KV

graph_bp = Blueprint("graph", __name__)


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
# GET /agentmemory/graph
# ---------------------------------------------------------------------------

@graph_bp.route("/agentmemory/graph", methods=["GET"])
def api_graph():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    result = functions.folder_graph_build(_get_kv())
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# GET /agentmemory/graph/stats
# ---------------------------------------------------------------------------

@graph_bp.route("/agentmemory/graph/stats", methods=["GET"])
def api_graph_stats():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    kv = _get_kv()
    sessions = functions.list_sessions(kv)
    memories = kv.list(KV.memories)

    folders = set()
    concepts_by_folder = {}

    for s in sessions:
        project = s.get("project", "").strip()
        if project:
            folders.add(project)

    for m in memories:
        project = m.get("project", "").strip()
        if project:
            folders.add(project)
            concepts = m.get("concepts", [])
            if concepts:
                if project not in concepts_by_folder:
                    concepts_by_folder[project] = set()
                for c in concepts:
                    if isinstance(c, str):
                        concepts_by_folder[project].add(c.lower())

    node_count = len(folders)
    edge_count = 0
    folder_list = list(folders)

    for i in range(len(folder_list)):
        for j in range(i + 1, len(folder_list)):
            f1 = folder_list[i]
            f2 = folder_list[j]

            c1 = concepts_by_folder.get(f1, set())
            c2 = concepts_by_folder.get(f2, set())
            shared = c1.intersection(c2)

            p1 = [p for p in f1.replace("\\", "/").split("/") if p]
            p2 = [p for p in f2.replace("\\", "/").split("/") if p]
            common_subdirs = 0
            for k in range(min(len(p1), len(p2))):
                if p1[k].lower() == p2[k].lower():
                    p_low = p1[k].lower()
                    if p_low not in ("c:", "d:", "downloads", "projects", "other projects"):
                        common_subdirs += 1
                else:
                    break

            if len(shared) > 0 or common_subdirs > 0:
                edge_count += 1

    return jsonify({"nodes": node_count, "edges": edge_count, "success": True}), 200


# ---------------------------------------------------------------------------
# POST /agentmemory/graph/query
# ---------------------------------------------------------------------------

@graph_bp.route("/agentmemory/graph/query", methods=["POST"])
def api_graph_query():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        # start_node_id = body.get("startNodeId")  # reserved for future use
        return jsonify({"nodes": [], "edges": [], "success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# POST /agentmemory/graph/build
# ---------------------------------------------------------------------------

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
