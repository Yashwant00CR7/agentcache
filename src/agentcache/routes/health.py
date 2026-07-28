"""
Health and audit routes blueprint.

Handles:
  GET /agentmemory/livez
  GET /agentmemory/health
  GET /agentmemory/audit
  GET /agentmemory/config/flags
"""

import os

from flask import Blueprint, Response, jsonify, request

from .. import legacy as functions
from ..legacy import query_audit
from ._deps import get_kv
from .auth import require_auth


def create_health_bp(kv=None, embedding_provider=None):
    """Blueprint factory — receives kv and embedding_provider at registration time."""
    bp = Blueprint("health", __name__)

    def _kv():
        return kv if kv is not None else get_kv()

    # ------------------------------------------------------------------
    # GET /auth.md  (no auth required)
    # ------------------------------------------------------------------

    @bp.route("/auth.md", methods=["GET"])
    def auth_docs():
        """Serve the agent onboarding documentation in Markdown format."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        auth_md_path = os.path.join(base_dir, "auth.md")

        if os.path.exists(auth_md_path):
            try:
                with open(auth_md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                return Response(
                    content,
                    mimetype="text/markdown",
                    headers={"Content-Type": "text/markdown; charset=utf-8"},
                )
            except Exception as e:
                return jsonify({"error": f"failed to read auth.md: {e}"}), 500

        return jsonify({"error": "auth.md not found"}), 404

    # ------------------------------------------------------------------
    # GET /agentcache/livez  (no auth required)
    # ------------------------------------------------------------------

    @bp.route("/agentcache/livez", methods=["GET"])
    @bp.route("/agentmemory/livez", methods=["GET"])
    def livez():
        port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
        return jsonify(
            {
                "status": "ok",
                "service": "agentcache",
                "viewerPort": port,
                "viewerSkipped": False,
            }
        )

    # ------------------------------------------------------------------
    # GET /agentcache/health
    # ------------------------------------------------------------------

    @bp.route("/agentcache/health", methods=["GET"])
    @bp.route("/agentmemory/health", methods=["GET"])
    def health():
        return jsonify(functions.health_check(_kv()))

    # ------------------------------------------------------------------
    # GET /agentcache/audit
    # ------------------------------------------------------------------

    @bp.route("/agentcache/audit", methods=["GET"])
    @bp.route("/agentmemory/audit", methods=["GET"])
    @require_auth
    def api_audit():
        op = request.args.get("operation")
        limit = int(request.args.get("limit", "50"))
        res = query_audit(_kv(), {"operation": op, "limit": limit})
        return jsonify({"entries": res, "success": True}), 200

    # ------------------------------------------------------------------
    # GET /agentcache/config/flags
    # ------------------------------------------------------------------

    @bp.route("/agentcache/config/flags", methods=["GET"])
    @bp.route("/agentmemory/config/flags", methods=["GET"])
    @require_auth
    def config_flags():
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
                "description": (
                    "Turns each observation into entities + relations so the /graph view "
                    "shows how your project's concepts connect. Needs an LLM provider key "
                    "to do the extraction (Gemini or OpenAI)."
                ),
                "enableHow": (
                    "# Add to  ~/.agentcache/.env   (Linux/macOS)\n"
                    "#     or  %USERPROFILE%\\.agentcache\\.env   (Windows)\n"
                    "GEMINI_API_KEY=...              # or OPENAI_API_KEY=sk-...\n"
                    "GRAPH_EXTRACTION_ENABLED=true\n"
                    "\n"
                    "# then restart the server:\n"
                    "agentcache serve"
                ),
                "docsHref": "https://github.com/Yashwant00CR7/agentcache#quickstart",
            },
            {
                "key": "CONSOLIDATION_ENABLED",
                "label": "Memory consolidation",
                "enabled": functions.is_consolidation_enabled(),
                "default": False,
                "affects": ["Dashboard", "Memories", "Crystals"],
                "needsLlm": True,
                "description": (
                    "Background worker that periodically summarises finished sessions into "
                    "semantic facts (long-term memories) and procedures. Needs an LLM key."
                ),
                "enableHow": (
                    "# Add to  ~/.agentcache/.env   (Linux/macOS)\n"
                    "#     or  %USERPROFILE%\\.agentcache\\.env   (Windows)\n"
                    "GEMINI_API_KEY=...              # or OPENAI_API_KEY=sk-...\n"
                    "CONSOLIDATION_ENABLED=true\n"
                    "\n"
                    "# then restart the server:\n"
                    "agentcache serve"
                ),
                "docsHref": "https://github.com/Yashwant00CR7/agentcache#quickstart",
            },
        ]
        return jsonify(
            {
                "version": "0.9.14",
                "provider": provider_kind,
                "embeddingProvider": embedding_prov,
                "flags": flags,
            }
        )

    return bp


health_bp = create_health_bp(None)
