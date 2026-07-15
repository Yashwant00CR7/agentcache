"""
MCP (Model Context Protocol) routes blueprint.

Handles:
  GET  /agentmemory/mcp/tools   — list all MCP tool schemas
  POST /agentmemory/mcp/tools   — dispatch a tool call
"""

import datetime
import json
import os

from flask import Blueprint, jsonify, request

from .. import functions
from ..functions import KV

mcp_bp = Blueprint("mcp", __name__)


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
    from .. import app as app_module

    return app_module.kv


def _datetime_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _parse_mcp_list_arg(arg_val):
    """Accept a list or comma-separated string and return a list of strings."""
    if isinstance(arg_val, list):
        return [str(item).strip() for item in arg_val if item]
    if isinstance(arg_val, str) and arg_val:
        return [item.strip() for item in arg_val.split(",") if item.strip()]
    return []


# ---------------------------------------------------------------------------
# GET /agentcache/mcp/tools
# ---------------------------------------------------------------------------


def get_mcp_tools_schemas():
    return [
        {
            "name": "cache_recall",
            "description": "Search past folder observations and global memories. Use when you need to recall what happened in a folder.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query keywords"},
                    "limit": {
                        "type": "number",
                        "description": "Max results to return (default 10)",
                    },
                    "folderPath": {
                        "type": "string",
                        "description": "Filter to a specific folder path (optional)",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Filter to a specific agent ID (optional)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "cache_smart_search",
            "description": "Hybrid semantic+keyword search across folder observations and global memories.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "number",
                        "description": "Max results (default 10)",
                    },
                    "folderPath": {
                        "type": "string",
                        "description": "Filter to a specific folder path (optional)",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Filter to a specific agent ID (optional)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "cache_save",
            "description": "Explicitly save an important insight, decision, or pattern to long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The insight or decision to remember",
                    },
                    "type": {
                        "type": "string",
                        "description": "Memory type: pattern, preference, architecture, bug, workflow, or fact",
                    },
                    "concepts": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "Comma-separated key concepts",
                            },
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of key concepts",
                            },
                        ]
                    },
                    "files": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "Comma-separated relevant file paths",
                            },
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of relevant file paths",
                            },
                        ]
                    },
                    "project": {
                        "type": "string",
                        "description": "Canonical project identifier",
                    },
                },
                "required": ["content"],
            },
        },
        {
            "name": "cache_diagnose",
            "description": "Health check — returns folder, agent, observation and memory counts.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "cache_forget",
            "description": "Delete a global memory or all observations for a (folderPath, agentId) pair.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memoryId": {
                        "type": "string",
                        "description": "Memory ID to delete",
                    },
                    "folderPath": {
                        "type": "string",
                        "description": "Folder path to delete observations from",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Agent ID to delete observations for",
                    },
                    "observationIds": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "Comma-separated observation IDs to delete",
                            },
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    },
                },
            },
        },
        {
            "name": "cache_export",
            "description": "Export all folder observations and global memories as JSON (v2 format).",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "agent_observe",
            "description": "Log an observation scoped to a folder path and agent ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folderPath": {
                        "type": "string",
                        "description": "Absolute path of the working directory",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Identity of the agent (e.g. 'kiro', 'claude')",
                    },
                    "text": {"type": "string", "description": "Observation content"},
                    "timestamp": {
                        "type": "string",
                        "description": "ISO 8601 UTC timestamp (defaults to now)",
                    },
                    "type": {"type": "string", "description": "Observation type"},
                    "title": {"type": "string", "description": "Short title"},
                    "concepts": {"type": "array", "items": {"type": "string"}},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer", "description": "1-10"},
                    "sessionId": {
                        "type": "string",
                        "description": "Deprecated — ignored",
                    },
                },
                "required": ["folderPath", "agentId", "text"],
            },
        },
        {
            "name": "agent_cache",
            "description": "Explicitly save a key insight, fact, or architecture decision to long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agentId": {
                        "type": "string",
                        "description": "ID/Name of the agent (optional)",
                    },
                    "content": {
                        "type": "string",
                        "description": "The memory content/insight",
                    },
                    "project": {
                        "type": "string",
                        "description": "Canonical project path/identifier",
                    },
                    "type": {
                        "type": "string",
                        "description": "Memory type: fact, preference, bug, workflow, architecture",
                    },
                    "concepts": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "Comma-separated key concepts",
                            },
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    },
                    "files": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": "Comma-separated relevant file paths",
                            },
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    },
                },
                "required": ["content"],
            },
        },
        {
            "name": "cache_folders",
            "description": "List all (folder, agent) pairs that have memory observations.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "cache_folder_observations",
            "description": "Get all observations for a specific (folderPath, agentId) pair.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folderPath": {"type": "string", "description": "Folder path"},
                    "agentId": {"type": "string", "description": "Agent ID"},
                },
                "required": ["folderPath", "agentId"],
            },
        },
        {
            "name": "cache_timeline",
            "description": "Get folder activity feed — observations sorted by time, filterable by folder/agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folderPath": {
                        "type": "string",
                        "description": "Filter by folder path (optional)",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Filter by agent ID (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 100)",
                    },
                    "before": {
                        "type": "string",
                        "description": "ISO timestamp upper bound (optional)",
                    },
                    "after": {
                        "type": "string",
                        "description": "ISO timestamp lower bound (optional)",
                    },
                },
            },
        },
        {
            "name": "cache_dedup",
            "description": "Remove duplicate observations from a (folderPath, agentId) pair or all pairs. Keeps the earliest observation per unique text fingerprint.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folderPath": {
                        "type": "string",
                        "description": "Folder path to deduplicate (optional — omit for all pairs)",
                    },
                    "agentId": {
                        "type": "string",
                        "description": "Agent ID to deduplicate (optional — omit for all pairs)",
                    },
                },
            },
        },
    ]


@mcp_bp.route("/agentcache/mcp/tools", methods=["GET"])
@mcp_bp.route("/agentmemory/mcp/tools", methods=["GET"])
def mcp_tools_list():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    tools = get_mcp_tools_schemas()
    return jsonify({"tools": tools}), 200


# ---------------------------------------------------------------------------
# POST /agentcache/mcp/tools
# ---------------------------------------------------------------------------


@mcp_bp.route("/agentcache/mcp/tools", methods=["POST"])
@mcp_bp.route("/agentmemory/mcp/tools", methods=["POST"])
def mcp_tools_call():
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    try:
        kv = _get_kv()
        body = request.get_json(force=True) or {}
        name = body.get("name")
        args = body.get("arguments") or {}
        if not name:
            return jsonify({"error": "name is required"}), 400

        print(f"[mcp] Calling tool {name} with args: {args}")
        text_out = ""

        if name in ("cache_recall", "memory_recall"):
            q = args.get("query")
            limit = int(args.get("limit") or 10)
            folder_path = args.get("folderPath")
            agent_id = args.get("agentId")
            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid:
                    if agent_id and agent_id != current_aid:
                        return jsonify(
                            {"error": "Unauthorized: Agent scope is isolated"}
                        ), 403
                    agent_id = current_aid
            res = functions.folder_search(
                kv, q, limit, folder_path=folder_path, agent_id=agent_id
            )
            text_out = json.dumps(res, indent=2)

        elif name in ("cache_save", "memory_save"):
            content = args.get("content")
            concepts = _parse_mcp_list_arg(args.get("concepts"))
            files = _parse_mcp_list_arg(args.get("files"))
            project = args.get("project")
            res = functions.remember(
                kv,
                {
                    "content": content,
                    "type": args.get("type") or "fact",
                    "concepts": concepts,
                    "files": files,
                    "project": project,
                },
            )
            text_out = json.dumps(res)

        elif name in ("cache_smart_search", "memory_smart_search"):
            q = args.get("query")
            limit = int(args.get("limit") or 10)
            folder_path = args.get("folderPath")
            agent_id = args.get("agentId")
            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid:
                    if agent_id and agent_id != current_aid:
                        return jsonify(
                            {"error": "Unauthorized: Agent scope is isolated"}
                        ), 403
                    agent_id = current_aid
            res = functions.folder_search(
                kv, q, limit, folder_path=folder_path, agent_id=agent_id
            )
            text_out = json.dumps(res, indent=2)

        elif name in ("cache_diagnose", "memory_diagnose"):
            res = functions.health_check(kv)
            text_out = json.dumps(res, indent=2)

        elif name in ("cache_forget", "memory_forget"):
            obs_ids = _parse_mcp_list_arg(args.get("observationIds"))
            res = functions.forget(
                kv,
                {
                    "memoryId": args.get("memoryId"),
                    "folderPath": args.get("folderPath"),
                    "agentId": args.get("agentId"),
                    "observationIds": obs_ids,
                },
            )
            text_out = json.dumps(res, indent=2)

        elif name in ("cache_export", "memory_export"):
            res = functions.export_data(kv, {})
            text_out = json.dumps(res, indent=2)

        elif name == "agent_observe":
            folder_path = args.get("folderPath")
            agent_id = args.get("agentId")
            text = args.get("text") or args.get("content") or ""

            # Compat shim: old plugin scripts send sessionId/project/cwd instead of
            # folderPath/agentId. Map them across so legacy callers keep working.
            if not folder_path:
                # Use cwd first, then project, then a sensible default
                folder_path = (
                    args.get("cwd")
                    or args.get("project")
                    or os.getenv("AGENTCACHE_CWD")
                    or os.getenv("AGENTMEMORY_CWD")
                    or "/unknown"
                )
            if not agent_id:
                agent_id = (
                    args.get("sessionId")
                    or functions.get_agent_id()
                    or os.getenv("AGENT_ID")
                    or "agent"
                )

            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid:
                    if agent_id and agent_id != current_aid:
                        return jsonify(
                            {"error": "Unauthorized: Agent scope is isolated"}
                        ), 403
                    agent_id = current_aid

            if not text:
                return jsonify({"error": "text (or content) is required"}), 400

            timestamp = args.get("timestamp") or _datetime_now_iso()
            payload = {
                "folderPath": folder_path,
                "agentId": agent_id,
                "text": text,
                "timestamp": timestamp,
                "type": args.get("type"),
                "title": args.get("title"),
                "concepts": args.get("concepts"),
                "files": args.get("files"),
                "importance": args.get("importance"),
            }
            res = functions.folder_observe(kv, payload)
            text_out = json.dumps(res)

        elif name in ("agent_cache", "agent_remember"):
            agent_id = args.get("agentId") or functions.get_agent_id() or "agent"
            content = args.get("content")
            project = args.get("project")
            mem_type = args.get("type") or "fact"
            concepts = _parse_mcp_list_arg(args.get("concepts"))
            files = _parse_mcp_list_arg(args.get("files"))

            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid:
                    if agent_id and agent_id != current_aid:
                        return jsonify(
                            {"error": "Unauthorized: Agent scope is isolated"}
                        ), 403
                    agent_id = current_aid

            if not content:
                return jsonify({"error": "content is required"}), 400
            payload = {
                "content": content,
                "type": mem_type,
                "concepts": concepts,
                "files": files,
                "project": project,
                "agentId": agent_id,
            }
            res = functions.remember(kv, payload)
            text_out = json.dumps(res)

        elif name in ("cache_folders", "memory_folders"):
            pairs = sorted(
                kv.list(KV.folders),
                key=lambda x: x.get("lastUpdated", ""),
                reverse=True,
            )
            if functions.is_agent_scope_isolated():
                aid = functions.get_agent_id()
                if aid:
                    pairs = [p for p in pairs if p.get("agentId") == aid]
            text_out = json.dumps(pairs, indent=2)

        elif name in ("cache_folder_observations", "memory_folder_observations"):
            fp = args.get("folderPath", "")
            aid = args.get("agentId", "")
            if not fp or not aid:
                return jsonify({"error": "folderPath and agentId are required"}), 400
            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid and aid != current_aid:
                    return jsonify(
                        {"error": "Unauthorized: Agent scope is isolated"}
                    ), 403
            obs = sorted(
                kv.list(KV.folder_obs(fp, aid)),
                key=lambda x: x.get("timestamp", ""),
                reverse=True,
            )
            text_out = json.dumps(obs, indent=2)

        elif name in ("cache_timeline", "memory_timeline"):
            request_aid = args.get("agentId")
            if functions.is_agent_scope_isolated():
                current_aid = functions.get_agent_id()
                if current_aid:
                    if request_aid and request_aid != current_aid:
                        return jsonify(
                            {"error": "Unauthorized: Agent scope is isolated"}
                        ), 403
                    request_aid = current_aid
            res = functions.folder_timeline(
                kv,
                limit=int(args.get("limit", 100)),
                folder_path=args.get("folderPath"),
                agent_id=request_aid,
                before=args.get("before"),
                after=args.get("after"),
            )
            text_out = json.dumps(res, indent=2)

        elif name in ("cache_dedup", "memory_dedup"):
            res = functions.dedup_folder_observations(
                kv,
                args.get("folderPath") or None,
                args.get("agentId") or None,
            )
            text_out = json.dumps(res, indent=2)

        else:
            return jsonify({"error": f"unknown tool: {name}"}), 400

        return jsonify({"content": [{"type": "text", "text": text_out}]}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
