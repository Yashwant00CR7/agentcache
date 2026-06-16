"""
Health and audit routes blueprint.

Handles:
  GET /agentmemory/livez
  GET /agentmemory/health
  GET /agentmemory/audit
  GET /agentmemory/config/flags
"""

import os
from flask import Blueprint, request, jsonify
import functions
from functions import query_audit

health_bp = Blueprint("health", __name__)


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


def _get_embedding_provider():
    import app as app_module
    return app_module.embedding_provider


# ---------------------------------------------------------------------------
# GET /agentcache/livez  (no auth required)
# ---------------------------------------------------------------------------

@health_bp.route("/agentcache/livez", methods=["GET"])
@health_bp.route("/agentmemory/livez", methods=["GET"])
def livez():
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    return jsonify({
        "status": "ok",
        "service": "agentcache",
        "viewerPort": port,
        "viewerSkipped": False,
    })


# ---------------------------------------------------------------------------
# GET /agentcache/health
# ---------------------------------------------------------------------------

@health_bp.route("/agentcache/health", methods=["GET"])
@health_bp.route("/agentmemory/health", methods=["GET"])
def health():
    return jsonify(functions.health_check(_get_kv()))


# ---------------------------------------------------------------------------
# GET /agentcache/audit
# ---------------------------------------------------------------------------

@health_bp.route("/agentcache/audit", methods=["GET"])
@health_bp.route("/agentmemory/audit", methods=["GET"])
def api_audit():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    op = request.args.get("operation")
    limit = int(request.args.get("limit", "50"))
    res = query_audit(_get_kv(), {"operation": op, "limit": limit})
    return jsonify({"entries": res, "success": True}), 200


# ---------------------------------------------------------------------------
# GET /agentcache/config/flags
# ---------------------------------------------------------------------------

@health_bp.route("/agentcache/config/flags", methods=["GET"])
@health_bp.route("/agentmemory/config/flags", methods=["GET"])
def config_flags():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    embedding_provider = _get_embedding_provider()
    provider_kind = "llm" if embedding_provider else "noop"
    embedding_prov = "gemini" if embedding_provider else "none"

    flags = [
        {
            "key": "GRAPH_EXTRACTION_ENABLED",
            "label": "Knowledge graph extraction",
            "enabled": functions.is_graph_extraction_enabled(),
            "default": False,
            "affects": ["Graph", "Dashboard"],
            "needsLlm": True,
            "description": "Extracts entities and relations from observations into a knowledge graph.",
            "enableHow": "Set GRAPH_EXTRACTION_ENABLED=true and restart.",
            "docsHref": "https://github.com/rohitg00/agentmemory#knowledge-graph",
        },
        {
            "key": "CONSOLIDATION_ENABLED",
            "label": "Memory consolidation",
            "enabled": functions.is_consolidation_enabled(),
            "default": False,
            "affects": ["Dashboard", "Memories", "Crystals"],
            "needsLlm": True,
            "description": "Periodically summarizes sessions into semantic facts + procedures.",
            "enableHow": "Set CONSOLIDATION_ENABLED=true and restart.",
            "docsHref": "https://github.com/rohitg00/agentmemory#consolidation",
        },
        {
            "key": "AGENTCACHE_AUTO_COMPRESS",
            "label": "LLM-powered observation compression",
            "enabled": functions.is_auto_compress_enabled(),
            "default": False,
            "affects": ["Memories", "Timeline"],
            "needsLlm": True,
            "description": "Every observation is compressed by the LLM for richer summaries. OFF uses synthetic compression.",
            "enableHow": "Set AGENTCACHE_AUTO_COMPRESS=true.",
            "docsHref": "https://github.com/rohitg00/agentmemory/issues/138",
        },
    ]
    return jsonify({
        "version": "0.9.8",
        "provider": provider_kind,
        "embeddingProvider": embedding_prov,
        "flags": flags,
    })
