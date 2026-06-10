import os
import sys
import json
import hmac
import secrets
import base64
import threading
from flask import Flask, request, jsonify, make_response, send_from_directory
from flask_sock import Sock

# Load environment variables from ~/.agentmemory/.env if it exists
def load_env():
    home = os.path.expanduser("~")
    env_path = os.path.join(home, ".agentmemory", ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        os.environ[k] = v
            print(f"[config] Loaded environment from {env_path}")
        except Exception as e:
            print(f"[config] Error reading env file: {e}")

load_env()

import datetime

def datetime_now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"

# Import local modules
from db import StateKV
import search
import functions
from functions import KV, query_audit

app = Flask(__name__)
# Enable CORS for all routes (important for client scripts connecting locally)
@app.after_request
def after_request(response):
    origin = request.headers.get("Origin")
    if origin:
        parsed_origin = origin.lower()
        is_allowed = False
        if parsed_origin in ("null", "vscode-webview://"):
            is_allowed = True
        elif parsed_origin.startswith("http://localhost:") or parsed_origin == "http://localhost":
            is_allowed = True
        elif parsed_origin.startswith("http://127.0.0.1:") or parsed_origin == "http://127.0.0.1":
            is_allowed = True
        elif parsed_origin.startswith("vscode-webview://"):
            is_allowed = True
        elif parsed_origin.startswith("chrome-extension://"):
            is_allowed = True
            
        if is_allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    return response

sock = Sock(app)

# Global instances
kv = None
embedding_provider = None
persistence = None

def init_app():
    global kv, embedding_provider, persistence
    
    # 1. Initialize DB
    kv = StateKV()
    
    # 2. Initialize Embedding Provider if API key is available
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            embedding_provider = search.GeminiEmbeddingProvider(api_key)
            functions.set_embedding_provider(embedding_provider)
            print(f"[search] Embedding provider active: gemini (768 dims)")
        except Exception as e:
            print(f"[search] Error initializing embedding provider: {e}")
    else:
        print(f"[search] No GEMINI_API_KEY found, running in BM25-only mode.")
        
    # 3. Persistence & load indexes
    persistence = functions.IndexPersistence(kv, functions._bm25_index, functions._vector_index if api_key else None)
    functions.set_index_persistence(persistence)
    
    loaded = persistence.load()
    print(f"[persistence] Load results: BM25 index={loaded['bm25']}, Vector index={loaded['vector']}")
    
    # 4. Rebuild index if empty
    if functions._bm25_index.size == 0:
        print("[persistence] Search index is empty. Rebuilding in background thread...")
        def run_rebuild():
            try:
                count = functions.rebuild_index(kv)
                print(f"[persistence] Rebuild completed: indexed {count} items.")
            except Exception as ex:
                print(f"[persistence] Rebuild failed: {ex}")
        t = threading.Thread(target=run_rebuild, daemon=True)
        t.start()

    # 5. Start background worker loops
    import time
    def run_auto_forget_loop():
        time.sleep(10)
        while True:
            try:
                if os.getenv("AUTO_FORGET_ENABLED") != "false":
                    print("[scheduler] Running auto_forget sweep...")
                    res = functions.auto_forget(kv, dry_run=False)
                    print(f"[scheduler] auto_forget sweep completed: {res}")
            except Exception as e:
                print(f"[scheduler] auto_forget loop error: {e}")
            time.sleep(3600)

    def run_consolidation_loop():
        time.sleep(15)
        while True:
            try:
                print("[scheduler] Running lesson_decay_sweep...")
                decay_res = functions.lesson_decay_sweep(kv)
                print(f"[scheduler] lesson_decay_sweep completed: {decay_res}")
                
                if functions.is_consolidation_enabled():
                    print("[scheduler] Running consolidation...")
                    cons_res = functions.consolidate(kv)
                    print(f"[scheduler] consolidation completed: {cons_res}")
            except Exception as e:
                print(f"[scheduler] consolidation/decay loop error: {e}")
            time.sleep(86400)

    t_forget = threading.Thread(target=run_auto_forget_loop, daemon=True)
    t_forget.start()
    
    t_consolidate = threading.Thread(target=run_consolidation_loop, daemon=True)
    t_consolidate.start()

# =====================================================================
# Auth Middleware
# =====================================================================

def timing_safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

def check_auth():
    secret = os.getenv("AGENTMEMORY_SECRET")
    if not secret:
        return None
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"error": "unauthorized"}), 401
    
    provided_token = auth[7:].strip()
    if not timing_safe_compare(provided_token, secret):
        return jsonify({"error": "unauthorized"}), 401
    return None

# =====================================================================
# WebSocket Streaming
# =====================================================================

active_websockets = set()

@sock.route("/stream/mem-live/viewer")
def stream_viewer(ws):
    secret = os.getenv("AGENTMEMORY_SECRET")
    if secret:
        token = request.args.get("token") or request.args.get("secret")
        if not token or not timing_safe_compare(token, secret):
            ws.close(1008)
            return

    active_websockets.add(ws)
    try:
        while True:
            data = ws.receive()
            if data is None:
                break
    except Exception:
        pass
    finally:
        active_websockets.discard(ws)

def broadcast_to_viewers(payload):
    msg = json.dumps(payload)
    for ws in list(active_websockets):
        try:
            ws.send(msg)
        except Exception:
            active_websockets.discard(ws)

# Register broadcaster in functions
functions.set_stream_broadcaster(broadcast_to_viewers)

# =====================================================================
# Viewer Routes
# =====================================================================

@app.route("/")
@app.route("/viewer")
@app.route("/agentmemory/viewer")
def serve_viewer():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(base_dir, "viewer", "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
            
        nonce = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("utf-8").rstrip("=")
        auto_token = os.environ.get("AGENTMEMORY_SECRET", "")
        html = (template
                .replace("__AGENTMEMORY_VIEWER_NONCE__", nonce)
                .replace("__AGENTMEMORY_VERSION__", "0.9.8")
                .replace("__AGENTMEMORY_AUTO_TOKEN__", auto_token))
        
        csp = "; ".join([
            "default-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self' https://huggingface.co https://*.hf.space",
            "object-src 'none'",
            "form-action 'none'",
            f"script-src 'nonce-{nonce}'",
            "script-src-attr 'none'",
            "style-src 'unsafe-inline'",
            "connect-src 'self' https: http://localhost:* http://127.0.0.1:* wss: ws://localhost:* ws://127.0.0.1:* wss://localhost:* wss://127.0.0.1:*",
            "img-src 'self' data:",
            "font-src 'self'",
        ])
        
        res = make_response(html)
        res.headers["Content-Type"] = "text/html; charset=utf-8"
        res.headers["Content-Security-Policy"] = csp
        res.headers["Cache-Control"] = "no-cache"
        return res
    except Exception as e:
        return f"Viewer not found: {e}", 404
 
@app.route("/favicon.svg")
def serve_favicon():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(os.path.join(base_dir, "viewer"), "favicon.svg")

# =====================================================================
# REST REST API Endpoints
# =====================================================================

@app.route("/agentmemory/livez", methods=["GET"])
def livez():
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    return jsonify({
        "status": "ok",
        "service": "agentmemory",
        "viewerPort": port,
        "viewerSkipped": False
    })

@app.route("/agentmemory/config/flags", methods=["GET"])
def config_flags():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
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
            "docsHref": "https://github.com/rohitg00/agentmemory#knowledge-graph"
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
            "docsHref": "https://github.com/rohitg00/agentmemory#consolidation"
        },
        {
            "key": "AGENTMEMORY_AUTO_COMPRESS",
            "label": "LLM-powered observation compression",
            "enabled": functions.is_auto_compress_enabled(),
            "default": False,
            "affects": ["Memories", "Timeline"],
            "needsLlm": True,
            "description": "Every observation is compressed by the LLM for richer summaries. OFF uses synthetic compression.",
            "enableHow": "Set AGENTMEMORY_AUTO_COMPRESS=true.",
            "docsHref": "https://github.com/rohitg00/agentmemory/issues/138"
        }
    ]
    return jsonify({
        "version": "0.9.8",
        "provider": provider_kind,
        "embeddingProvider": embedding_prov,
        "flags": flags
    })

@app.route("/agentmemory/health", methods=["GET"])
def health():
    return jsonify(functions.health_check(kv))

@app.route("/agentmemory/observe", methods=["POST"])
def api_observe():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.observe(kv, body)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/agent/observe", methods=["POST"])
def api_agent_observe():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        agent_id = body.get("agentId")
        session_id = body.get("sessionId")
        project = body.get("project")
        cwd = body.get("cwd") or ""
        text = body.get("text") or body.get("content") or ""
        obs_type = body.get("type") or "other"
        title = body.get("title") or f"agent_{obs_type}"
        image_data = body.get("imageData")
        
        if not session_id or not project:
            return jsonify({"error": "sessionId and project are required"}), 400
            
        timestamp = datetime_now_iso()
        
        data_payload = {
            "tool_name": title,
            "tool_input": text,
            "tool_output": text,
        }
        if image_data:
            data_payload["imageBase64"] = image_data
            
        payload = {
            "sessionId": session_id,
            "project": project,
            "cwd": cwd,
            "hookType": "post_tool_use",
            "timestamp": timestamp,
            "data": data_payload
        }
        if agent_id:
            payload["agentId"] = agent_id
            
        res = functions.observe(kv, payload)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/agentmemory/context", methods=["POST"])
def api_context():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.context(kv, body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/search", methods=["POST"])
def api_search():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        query = body.get("query")
        if not query or not query.strip():
            return jsonify({"error": "query is required"}), 400
        limit = body.get("limit") or 10
        
        # smart search / hybrid search query
        res = functions._hybrid_search.search(query, limit)
        res = enrich_search_results(kv, res)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/export", methods=["GET"])
def api_export():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    try:
        max_sess = request.args.get("maxSessions")
        offset = request.args.get("offset")
        payload = {}
        if max_sess is not None: payload["maxSessions"] = max_sess
        if offset is not None: payload["offset"] = offset
        res = functions.export_data(kv, payload)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/agentmemory/replay/sessions", methods=["GET"])
def api_replay_sessions():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    sessions = functions.list_sessions(kv)
    return jsonify({"success": True, "sessions": sessions}), 200

@app.route("/agentmemory/session/start", methods=["POST"])
def api_session_start():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        session_id = body.get("sessionId")
        project = body.get("project")
        cwd = body.get("cwd")
        if not session_id or not project or not cwd:
            return jsonify({"error": "sessionId, project, and cwd are required"}), 400
            
        title = body.get("title")
        agent_id = body.get("agentId") or functions.get_agent_id()
        
        session = {
            "id": session_id,
            "project": project,
            "cwd": cwd,
            "startedAt": datetime_now_iso(),
            "status": "active",
            "observationCount": 0
        }
        if title:
            session["summary"] = title[:200]
            session["firstPrompt"] = title[:200]
        if agent_id:
            session["agentId"] = agent_id
            
        functions.create_session(kv, session)
        
        # Compile initial context
        ctx = functions.context(kv, {"sessionId": session_id, "project": project})
        
        # Commit to Dolt
        functions.commit_if_enabled(kv, f"Start session {session_id[:8]}", agent_id)
        
        return jsonify({"session": session, "context": ctx.get("context", "")}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/antigravity/sync", methods=["POST"])
def api_antigravity_sync():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        mode = body.get("mode") or "current_session"
        current_convo = body.get("currentConversationId")
        current_folder = body.get("currentFolder")
        res = perform_antigravity_sync(mode, current_convo, current_folder)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/agentmemory/session/end", methods=["POST"])
def api_session_end():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        session_id = body.get("sessionId")
        if not session_id:
            return jsonify({"error": "sessionId is required"}), 400
            
        functions.end_session(kv, session_id)
        
        # Commit to Dolt
        sess = functions.get_session(kv, session_id) or {}
        agent_id = sess.get("agentId")
        functions.commit_if_enabled(kv, f"End session {session_id[:8]}", agent_id)
        
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/session/commit", methods=["POST"])
def api_session_commit():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        sha = body.get("sha")
        if not sha:
            return jsonify({"error": "sha is required"}), 400
            
        session_id = body.get("sessionId")
        branch = body.get("branch")
        repo = body.get("repo")
        message = body.get("message")
        author = body.get("author")
        authored_at = body.get("authoredAt")
        files = body.get("files")
        
        existing = kv.get(KV.commits, sha) or {}
        session_ids = set(existing.get("sessionIds", []))
        if session_id:
            session_ids.add(session_id)
            
        link = {
            "sha": sha,
            "shortSha": sha[:7],
            "branch": branch or existing.get("branch"),
            "repo": repo or existing.get("repo"),
            "message": message or existing.get("message"),
            "author": author or existing.get("author"),
            "authoredAt": authored_at or existing.get("authoredAt"),
            "files": files or existing.get("files"),
            "sessionIds": list(session_ids),
            "linkedAt": existing.get("linkedAt") or datetime_now_iso()
        }
        kv.set(KV.commits, sha, link)
        
        if session_id:
            sess = functions.get_session(kv, session_id)
            if sess:
                commit_shas = set(sess.get("commitShas", []))
                commit_shas.add(sha)
                sess["commitShas"] = list(commit_shas)
                functions.create_session(kv, sess)
                
        return jsonify({"commit": link}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/session/by-commit", methods=["GET"])
def api_session_by_commit():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    sha = request.args.get("sha")
    if not sha:
        return jsonify({"error": "sha query param required"}), 400
        
    link = kv.get(KV.commits, sha)
    if not link:
        return jsonify({"error": "no sessions linked to this commit"}), 404
        
    sessions = []
    for sid in link.get("sessionIds", []):
        sess = functions.get_session(kv, sid)
        if sess:
            sessions.append(sess)
    return jsonify({"commit": link, "sessions": sessions}), 200

@app.route("/agentmemory/commits", methods=["GET"])
def api_commits():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    branch = request.args.get("branch")
    repo = request.args.get("repo")
    limit = int(request.args.get("limit", "100"))
    
    all_links = kv.list(KV.commits)
    filtered = all_links
    if branch:
        filtered = [c for c in filtered if c.get("branch") == branch]
    if repo:
        filtered = [c for c in filtered if c.get("repo") == repo]
        
    filtered.sort(key=lambda c: c.get("linkedAt", ""), reverse=True)
    return jsonify({"commits": filtered[:limit]}), 200

@app.route("/agentmemory/sessions", methods=["GET"])
def api_sessions():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    sessions = functions.list_sessions(kv)
    agent_id = request.args.get("agentId")
    if agent_id and agent_id != "*":
        sessions = [s for s in sessions if s.get("agentId") == agent_id]
    elif functions.is_agent_scope_isolated():
        env_aid = functions.get_agent_id()
        if env_aid:
            sessions = [s for s in sessions if s.get("agentId") == env_aid]
            
    return jsonify({"sessions": sessions}), 200

@app.route("/agentmemory/observations", methods=["GET"])
def api_observations():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    session_id = request.args.get("sessionId")
    if not session_id:
        return jsonify({"error": "sessionId is required"}), 400
        
    obs = kv.list(KV.observations(session_id))
    obs.sort(key=lambda o: o.get("timestamp", ""))
    agent_id = request.args.get("agentId")
    if agent_id and agent_id != "*":
        obs = [o for o in obs if o.get("agentId") == agent_id]
    elif functions.is_agent_scope_isolated():
        env_aid = functions.get_agent_id()
        if env_aid:
            obs = [o for o in obs if o.get("agentId") == env_aid]
            
    return jsonify({"observations": obs}), 200

@app.route("/agentmemory/remember", methods=["POST"])
def api_remember():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.remember(kv, body)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/agent/remember", methods=["POST"])
def api_agent_remember():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        content = body.get("content")
        if not content:
            return jsonify({"error": "content is required"}), 400
            
        agent_id = body.get("agentId")
        project = body.get("project")
        mem_type = body.get("type") or "fact"
        
        concepts = body.get("concepts") or []
        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]
            
        files = body.get("files") or []
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
            
        payload = {
            "content": content,
            "type": mem_type,
            "concepts": concepts,
            "files": files,
            "project": project
        }
        if agent_id:
            payload["agentId"] = agent_id
            
        res = functions.remember(kv, payload)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/agentmemory/forget", methods=["POST"])
def api_forget():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.forget(kv, body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# =====================================================================
# Lessons Learned Endpoints
# =====================================================================

@app.route("/agentmemory/lessons", methods=["GET"])
def api_lessons_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    project = request.args.get("project")
    source = request.args.get("source")
    min_conf = float(request.args.get("minConfidence", "0"))
    limit = int(request.args.get("limit", "50"))
    
    res = functions.lesson_list(kv, {
        "project": project,
        "source": source,
        "minConfidence": min_conf,
        "limit": limit
    })
    return jsonify(res), 200

@app.route("/agentmemory/lessons", methods=["POST"])
def api_lessons_save():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.lesson_save(kv, body)
        return jsonify(res), 201 if res.get("action") == "created" else 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/lessons/search", methods=["POST"])
def api_lessons_search():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.lesson_recall(kv, body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/lessons/strengthen", methods=["POST"])
def api_lessons_strengthen():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        lesson_id = body.get("lessonId")
        if not lesson_id:
            return jsonify({"error": "lessonId is required"}), 400
        res = functions.lesson_strengthen(kv, lesson_id)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# =====================================================================
# Memory Slots Endpoints
# =====================================================================

@app.route("/agentmemory/slots", methods=["GET"])
def api_slots_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    return jsonify(functions.slot_list(kv)), 200

@app.route("/agentmemory/slot", methods=["GET"])
def api_slots_get():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    label = request.args.get("label")
    if not label:
        return jsonify({"error": "label required"}), 400
    res = functions.slot_get(kv, label)
    status = 200 if res.get("success") else 404
    return jsonify(res), status

@app.route("/agentmemory/slot", methods=["POST"])
def api_slots_create():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.slot_create(kv, body)
        status = 201 if res.get("success") else 400
        return jsonify(res), status
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/slot/append", methods=["POST"])
def api_slots_append():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        label = body.get("label")
        text = body.get("text")
        if not label or not text:
            return jsonify({"error": "label and text required"}), 400
        res = functions.slot_append(kv, label, text)
        status = 200 if res.get("success") else 400
        return jsonify(res), status
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/slot/replace", methods=["POST"])
def api_slots_replace():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        label = body.get("label")
        content = body.get("content")
        if not label or content is None:
            return jsonify({"error": "label and content required"}), 400
        res = functions.slot_replace(kv, label, content)
        status = 200 if res.get("success") else 400
        return jsonify(res), status
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/slot", methods=["DELETE"])
def api_slots_delete():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    label = request.args.get("label")
    if not label:
        return jsonify({"error": "label query param required"}), 400
    res = functions.slot_delete(kv, label)
    status = 200 if res.get("success") else 404
    return jsonify(res), status

@app.route("/agentmemory/slot/reflect", methods=["POST"])
def api_slots_reflect():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        session_id = body.get("sessionId")
        if not session_id:
            return jsonify({"error": "sessionId required"}), 400
        max_obs = body.get("maxObservations") or 50
        res = functions.slot_reflect(kv, session_id, max_obs)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# =====================================================================
# Audit, Relations, Evolve, Timeline, Profile
# =====================================================================

@app.route("/agentmemory/audit", methods=["GET"])
def api_audit():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    op = request.args.get("operation")
    limit = int(request.args.get("limit", "50"))
    res = query_audit(kv, {"operation": op, "limit": limit})
    return jsonify({"entries": res, "success": True}), 200

@app.route("/agentmemory/relations", methods=["GET"])
def api_relations_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    rels = functions.get_relations(kv)
    return jsonify({"relations": rels}), 200

@app.route("/agentmemory/relations", methods=["POST"])
def api_relations_add():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.add_relation(kv, body)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/evolve", methods=["POST"])
def api_evolve():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.evolve_memory(kv, body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/timeline", methods=["POST"])
def api_timeline():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.timeline(kv, body)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/profile", methods=["GET"])
def api_profile():
    auth_err = check_auth()
    if auth_err:
        return auth_err

    project = request.args.get("project")
    if not project:
        sessions = kv.list(KV.sessions)
        projects = set(s.get("project", "") for s in sessions if s.get("project"))
        memories = kv.list(KV.memories)
        mem_projects = set(m.get("project", "") for m in memories if m.get("project"))
        all_projects = sorted(projects.union(mem_projects))
        return jsonify({"projects": all_projects, "success": True}), 200

    res = functions.get_project_profile(kv, project)
    return jsonify(res), 200

# =====================================================================
# Missing endpoints the JS viewer calls
# =====================================================================

@app.route("/agentmemory/memories", methods=["GET"])
def api_memories_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err

    latest_only = request.args.get("latest", "false").lower() == "true"
    limit = int(request.args.get("limit", "500"))
    all_mems = kv.list(KV.memories)
    if latest_only:
        all_mems = [m for m in all_mems if m.get("isLatest") is not False]
    all_mems.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    return jsonify({"memories": all_mems[:limit], "total": len(all_mems)}), 200

@app.route("/agentmemory/graph/stats", methods=["GET"])
def api_graph_stats():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    nodes = kv.list(KV.graphNodes)
    edges = kv.list(KV.graphEdges)
    return jsonify({"nodes": len(nodes), "edges": len(edges), "success": True}), 200

@app.route("/agentmemory/semantic", methods=["GET"])
def api_semantic_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    items = kv.list(KV.semantic)
    return jsonify({"items": items, "total": len(items)}), 200

@app.route("/agentmemory/procedural", methods=["GET"])
def api_procedural_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    items = kv.list(KV.procedural)
    return jsonify({"items": items, "total": len(items)}), 200

@app.route("/agentmemory/crystals", methods=["GET"])
def api_crystals_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    items = kv.list(KV.crystals)
    return jsonify({"crystals": items, "total": len(items)}), 200


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def get_file_context(kv_inst, session_id, files, project):
    try:
        sessions = kv_inst.list(KV.sessions)
        other_sessions = [s for s in sessions if s.get("id") != session_id] if session_id else sessions
        if project:
            other_sessions = [s for s in other_sessions if s.get("project") == project]
        
        # Sort sessions by startedAt desc
        other_sessions.sort(key=lambda s: s.get("startedAt") or "", reverse=True)
        other_sessions = other_sessions[:15]
        
        obs_cache = {}
        for s in other_sessions:
            s_id = s["id"]
            obs_cache[s_id] = kv_inst.list(KV.observations(s_id))
            
        results = []
        for file in files:
            history = {"file": file, "observations": []}
            normalized_file = file.replace("./", "", 1)
            
            for s in other_sessions:
                s_id = s["id"]
                observations = obs_cache.get(s_id) or []
                
                for obs in observations:
                    obs_files = obs.get("files") or []
                    if isinstance(obs_files, str):
                        obs_files = [f.strip() for f in obs_files.split(",") if f.strip()]
                    obs_title = obs.get("title")
                    if not obs_files or not obs_title:
                        continue
                    
                    # Check match
                    matches = False
                    for f in obs_files:
                        if f == file or f == normalized_file or f.endswith(f"/{normalized_file}") or normalized_file.endswith(f"/{f}"):
                            matches = True
                            break
                    
                    importance = int(obs.get("importance") or 0)
                    if matches and importance >= 4:
                        history["observations"].append({
                            "sessionId": s_id,
                            "obsId": obs.get("id"),
                            "type": obs.get("type", "other"),
                            "title": obs_title,
                            "narrative": obs.get("narrative") or obs.get("content") or "",
                            "importance": importance,
                            "timestamp": obs.get("timestamp", "")
                        })
            
            # Sort by importance desc
            history["observations"].sort(key=lambda o: o["importance"], reverse=True)
            history["observations"] = history["observations"][:5]
            if history["observations"]:
                results.append(history)
                
        if not results:
            return ""
            
        lines = ["<agentmemory-file-context>"]
        for fh in results:
            lines.append(f"## {fh['file']}")
            for obs in fh["observations"]:
                lines.append(f"- [{obs['type']}] {obs['title']}: {obs['narrative']}")
        lines.append("</agentmemory-file-context>")
        return "\n".join(lines)
    except Exception as e:
        print(f"[enrich] Error generating file context: {e}")
        return ""


@app.route("/agentmemory/enrich", methods=["POST"])
def api_enrich():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("sessionId")
        files = data.get("files") or []
        terms = data.get("terms") or []
        project = data.get("project")
        
        parts = []
        # 1. File Context
        if files:
            file_context = get_file_context(kv, session_id, files, project)
            if file_context:
                parts.append(file_context)
                
        # 2. Search
        search_queries = [os.path.basename(f) for f in files] + terms
        search_queries = [q for q in search_queries if q]
        if search_queries:
            query_str = " ".join(search_queries)
            res = functions._hybrid_search.search(query_str, 5)
            if project:
                res = [r for r in res if r.get("observation", {}).get("project") == project or r.get("project") == project]
            
            obs_texts = []
            for r in res:
                obs = r.get("observation", r)
                narrative = obs.get("narrative") or obs.get("content")
                if narrative:
                    obs_texts.append(escape_xml(narrative))
            if obs_texts:
                parts.append("<agentmemory-relevant-context>\n" + "\n".join(obs_texts) + "\n</agentmemory-relevant-context>")
                
        # 3. Bug memories
        if files:
            bugs = []
            memories = kv.list(KV.memories)
            for m in memories:
                m_type = m.get("type")
                m_is_latest = m.get("isLatest", True)
                m_project = m.get("project")
                m_files = m.get("files") or []
                if isinstance(m_files, str):
                    m_files = [f.strip() for f in m_files.split(",") if f.strip()]
                
                if m_type == "bug" and m_is_latest:
                    if project and m_project and m_project != project:
                        continue
                    
                    has_overlap = False
                    for f in m_files:
                        for df in files:
                            if f in df or df in f:
                                has_overlap = True
                                break
                        if has_overlap:
                            break
                    
                    if has_overlap:
                        bugs.append(m)
            
            bugs.sort(key=lambda m: m.get("updatedAt") or m.get("createdAt") or "", reverse=True)
            bugs_lines = []
            for m in bugs[:3]:
                title = escape_xml(m.get("title") or "")
                content = escape_xml(m.get("content") or "")
                bugs_lines.append(f"- {title}: {content}")
            if bugs_lines:
                parts.append("<agentmemory-past-errors>\n" + "\n".join(bugs_lines) + "\n</agentmemory-past-errors>")
                
        context = "\n\n".join(parts)
        if len(context) > 4000:
            context = context[:4000]
            
        return jsonify({"context": context}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/agentmemory/crystals/auto", methods=["POST"])
def api_crystals_auto():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    return jsonify({"success": True, "message": "auto-crystallize stub (not implemented in Python version)"}), 200


@app.route("/agentmemory/claude-bridge/sync", methods=["POST"])
def api_claude_bridge_sync():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    return jsonify({"success": True, "message": "claude-bridge/sync stub"}), 200

@app.route("/agentmemory/actions", methods=["GET"])
def api_actions_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    limit = int(request.args.get("limit", "200"))
    status = request.args.get("status")
    items = kv.list(KV.actions)
    if status:
        items = [a for a in items if a.get("status") == status]
    items.sort(key=lambda a: a.get("updatedAt", ""), reverse=True)
    return jsonify({"actions": items[:limit], "total": len(items)}), 200

@app.route("/agentmemory/actions", methods=["POST"])
def api_action_create():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        action_id = functions.generate_id("act")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        action = {
            "id": action_id,
            "title": body.get("title", ""),
            "description": body.get("description"),
            "priority": body.get("priority", 0),
            "status": body.get("status", "pending"),
            "tags": body.get("tags", []),
            "sessionId": body.get("sessionId"),
            "createdAt": now,
            "updatedAt": now,
        }
        kv.set(KV.actions, action_id, action)
        return jsonify({"action": action, "success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/actions/<action_id>", methods=["PATCH"])
def api_action_update(action_id):
    auth_err = check_auth()
    if auth_err:
        return auth_err
    try:
        body = request.get_json(force=True) or {}
        existing = kv.get(KV.actions, action_id)
        if not existing:
            return jsonify({"error": "not found"}), 404
        from datetime import datetime, timezone
        allowed_fields = {"title", "description", "priority", "status", "tags", "sessionId"}
        updates = {k: v for k, v in body.items() if k in allowed_fields}
        existing.update(updates)
        existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
        kv.set(KV.actions, action_id, existing)
        return jsonify({"action": existing, "success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/frontier", methods=["GET"])
def api_frontier():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    items = kv.list(KV.actions)
    frontier = [a for a in items if a.get("status") in ("pending", "active")]
    frontier.sort(key=lambda a: (-(a.get("priority") or 0), a.get("createdAt", "")))
    return jsonify({"frontier": frontier[:50], "total": len(frontier)}), 200

@app.route("/agentmemory/insights", methods=["GET"])
def api_insights_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    limit = int(request.args.get("limit", "200"))
    items = kv.list(KV.insights)
    items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return jsonify({"insights": items[:limit], "total": len(items)}), 200

@app.route("/agentmemory/replay/load", methods=["GET"])
def api_replay_load():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    session_id = request.args.get("sessionId")
    if not session_id:
        return jsonify({"error": "sessionId required"}), 400
    session = functions.get_session(kv, session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    obs = kv.list(KV.observations(session_id))
    obs.sort(key=lambda o: o.get("timestamp", ""))

    from dateutil import parser as dtparser
    session_start = session.get("startedAt", "")
    try:
        t0_ms = dtparser.parse(session_start).timestamp() * 1000 if session_start else None
    except Exception:
        t0_ms = None

    _hook_kind = {
        "prompt_submit": "prompt",
        "post_tool_use": "tool_call",
        "post_tool_failure": "tool_error",
        "subagent_stop": "response",
        "task_completed": "response",
        "notification": "response",
    }

    events = []
    for o in obs:
        ts = o.get("timestamp", "")
        try:
            t_ms = dtparser.parse(ts).timestamp() * 1000 if ts else 0
        except Exception:
            t_ms = 0
        offset_ms = max(0, t_ms - t0_ms) if t0_ms is not None else t_ms

        hook = o.get("hookType", "")
        kind = _hook_kind.get(hook, "prompt")

        # obs fields are top-level (no data wrapper)
        tool_name = o.get("toolName")
        tool_input = o.get("toolInput")
        tool_output = o.get("toolOutput")

        label = (o.get("userPrompt") or
                 o.get("narrative") or o.get("title") or o.get("summary") or
                 tool_name or
                 (o.get("raw") or "")[:80] or hook)
        if isinstance(label, str):
            label = label[:120]

        events.append({
            "kind": kind,
            "label": label,
            "offsetMs": offset_ms,
            "ts": ts,
            "body": o.get("narrative") or o.get("raw") or o.get("summary") or "",
            "toolName": tool_name,
            "toolInput": tool_input,
            "toolOutput": tool_output,
        })

    total_ms = (events[-1]["offsetMs"] + 1000) if events else 0
    timeline = {"events": events, "eventCount": len(events), "totalDurationMs": total_ms}
    return jsonify({"session": session, "timeline": timeline, "success": True}), 200

@app.route("/agentmemory/replay/import-jsonl", methods=["POST"])
def api_replay_import_jsonl():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        path = body.get("path")
        max_files = body.get("maxFiles")
        
        import replay_import
        res = replay_import.import_jsonl_data(kv, path=path, max_files=max_files)
        
        status_code = 200 if res.get("success", True) else 400
        return jsonify(res), status_code
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route("/agentmemory/auto-forget", methods=["POST"])
def api_auto_forget():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        dry_run = body.get("dryRun", False)
        res = functions.auto_forget(kv, dry_run)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/summarize", methods=["POST"])
def api_summarize():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        res = functions.summarize(kv, body)
        if not res.get("success"):
            return jsonify(res), 400
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/consolidate", methods=["POST"])
def api_consolidate():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        project = body.get("project")
        min_obs = body.get("minObservations")
        res = functions.consolidate(kv, project=project, min_observations=min_obs)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/consolidate-pipeline", methods=["POST"])
def api_consolidate_pipeline():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        res = functions.consolidate(kv)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/dolt/commits", methods=["GET"])
def api_dolt_commits():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        limit = int(request.args.get("limit", "50"))
        rows = kv.get_audit_log(limit)
        commits = []
        for r in rows:
            ts_ms = r["ts"]
            iso = datetime.datetime.utcfromtimestamp(ts_ms / 1000).isoformat() + "Z"
            row_id = str(r["id"])
            commits.append({
                "sha": row_id.zfill(40),
                "shortSha": row_id,
                "agent": r["agent_id"],
                "email": f"{r['agent_id']}@agentmemory.ai",
                "date": iso,
                "message": r["message"]
            })
        return jsonify({"success": True, "commits": commits}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/agentmemory/second-brain", methods=["GET"])
def api_get_second_brain():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    brain_dir = os.getenv("SECOND_BRAIN_DIR", os.path.join(os.path.expanduser("~"), ".agentmemory", "second-brain"))
    os.makedirs(brain_dir, exist_ok=True)
        
    file_param = request.args.get("file")
    if file_param:
        safe_name = os.path.basename(file_param)
        if not safe_name.endswith(".md"):
            return jsonify({"error": "Only markdown files are allowed"}), 400
        file_path = os.path.join(brain_dir, safe_name)
        if not os.path.exists(file_path):
            return jsonify({"error": f"File {safe_name} not found"}), 404
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            return jsonify({"file": safe_name, "content": content}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    try:
        files = []
        for name in os.listdir(brain_dir):
            if name.endswith(".md") and os.path.isfile(os.path.join(brain_dir, name)):
                file_path = os.path.join(brain_dir, name)
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                files.append({
                    "name": name,
                    "size": os.path.getsize(file_path),
                    "content": content
                })
        return jsonify({"success": True, "path": brain_dir, "files": files}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/agentmemory/second-brain", methods=["POST"])
def api_update_second_brain():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        file_name = body.get("file")
        content = body.get("content")
        
        if not file_name or content is None:
            return jsonify({"error": "file and content are required"}), 400
            
        safe_name = os.path.basename(file_name)
        if not safe_name.endswith(".md"):
            return jsonify({"error": "Only markdown files are allowed"}), 400
            
        brain_dir = os.getenv("SECOND_BRAIN_DIR", os.path.join(os.path.expanduser("~"), ".agentmemory", "second-brain"))
        if not os.path.exists(brain_dir):
            os.makedirs(brain_dir, exist_ok=True)
            
        file_path = os.path.join(brain_dir, safe_name)
        
        content = functions.strip_private_data(content)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        functions.commit_if_enabled(kv, f"Update second-brain file: {safe_name}", "user")
        
        return jsonify({"success": True, "file": safe_name, "size": len(content)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =====================================================================
# MCP Server Integration (JSON-RPC)
# =====================================================================

def parse_mcp_list_arg(arg_val):
    if isinstance(arg_val, list):
        return [str(item).strip() for item in arg_val if item]
    if isinstance(arg_val, str) and arg_val:
        return [item.strip() for item in arg_val.split(",") if item.strip()]
    return []

def enrich_search_results(kv_inst, results):
    enriched = []
    for r in results:
        item = dict(r)
        obs_id = item.get("obsId")
        session_id = item.get("sessionId")
        if not obs_id:
            continue
            
        obj = None
        if session_id == "memory" or obs_id.startswith("mem_"):
            obj = kv_inst.get(KV.memories, obs_id)
        elif session_id:
            obj = kv_inst.get(KV.observations(session_id), obs_id)
            
        if obj:
            item["title"] = obj.get("title") or ""
            content_val = obj.get("content") or obj.get("narrative") or obj.get("raw") or ""
            if not isinstance(content_val, str):
                try:
                    content_val = json.dumps(content_val)
                except Exception:
                    content_val = str(content_val)
            item["content"] = content_val
            item["type"] = obj.get("type") or ""
            item["concepts"] = obj.get("concepts") or []
            item["files"] = obj.get("files") or []
        else:
            item["title"] = ""
            item["content"] = ""
            item["type"] = ""
            item["concepts"] = []
            item["files"] = []
        enriched.append(item)
    return enriched

def perform_antigravity_sync(mode="current_session", current_conversation_id=None, current_folder=None):
    import os
    import json
    import glob
    import re
    
    brain_dir = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain")
    if not os.path.exists(brain_dir):
        return {"success": False, "syncedSessions": [], "observationsAdded": 0, "error": f"Brain directory not found at {brain_dir}"}

    pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
    files = glob.glob(pattern)
    if not files:
        return {"success": True, "syncedSessions": [], "observationsAdded": 0}

    conversations = []
    for fpath in files:
        try:
            mtime = os.path.getmtime(fpath)
            convo_id = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(fpath))))
            conversations.append({
                "id": convo_id,
                "transcriptPath": fpath,
                "mtime": mtime
            })
        except Exception:
            pass

    if not conversations:
        return {"success": True, "syncedSessions": [], "observationsAdded": 0}

    conversations.sort(key=lambda x: x["mtime"], reverse=True)

    targets = []
    if mode == "current_session":
        if current_conversation_id:
            match = next((c for c in conversations if c["id"] == current_conversation_id), None)
            if match:
                targets = [match]
        else:
            targets = [conversations[0]]
    elif mode == "current_folder":
        target_folder = current_folder if current_folder else ""
        if not target_folder:
            target_folder = os.getcwd()
            
        target_folder_norm = target_folder.replace("\\", "/").lower().strip()
        
        for convo in conversations:
            try:
                with open(convo["transcriptPath"], "r", encoding="utf-8") as tf:
                    text = tf.read().lower()
                    text_norm = text.replace("\\/", "/").replace("\\\\", "/")
                    if target_folder_norm in text_norm:
                        targets.append(convo)
            except Exception:
                pass
    elif mode == "all":
        targets = conversations
    else:
        return {"success": False, "syncedSessions": [], "observationsAdded": 0, "error": f"Invalid mode: {mode}"}

    if not targets:
        return {"success": True, "syncedSessions": [], "observationsAdded": 0}

    synced_sessions = []
    observations_added = 0

    for convo in targets:
        convo_id = convo["id"]
        tpath = convo["transcriptPath"]
        session_id = f"antigravity_{convo_id[:18].replace('-', '_')}"
        
        project_path = None
        try:
            with open(tpath, "r", encoding="utf-8") as tf:
                first_line = tf.readline()
                if first_line:
                    step = json.loads(first_line)
                    match = re.search(r"\[([^\]]+)\]\s*->\s*\[([^\]]+)\]", step.get("content", ""))
                    if match:
                        project_path = match.group(2)
        except Exception:
            pass
            
        if not project_path:
            project_path = os.getcwd()

        turns = []
        current_prompt = None
        current_timestamp = None

        try:
            with open(tpath, "r", encoding="utf-8") as tf:
                for line in tf:
                    if not line.strip():
                        continue
                    try:
                        step = json.loads(line)
                        step_type = step.get("type")
                        if step_type == "USER_INPUT":
                            p_text = step.get("content", "")
                            if "<USER_REQUEST>" in p_text:
                                parts = p_text.split("<USER_REQUEST>")
                                if len(parts) > 1:
                                    p_text = parts[1]
                            if "</USER_REQUEST>" in p_text:
                                p_text = p_text.split("</USER_REQUEST>")[0]
                            current_prompt = p_text.strip()
                            current_timestamp = step.get("created_at")
                        elif step_type == "PLANNER_RESPONSE" and current_prompt:
                            turns.append({
                                "prompt": current_prompt,
                                "response": step.get("content", ""),
                                "timestamp": current_timestamp or step.get("created_at") or datetime_now_iso()
                            })
                            current_prompt = None
                            current_timestamp = None
                    except Exception:
                        pass
        except Exception:
            continue

        if not turns:
            continue

        existing_inputs = set()
        obs_list = kv.list(KV.observations(session_id))
        for obs in obs_list:
            tool_input = obs.get("toolInput") or (obs.get("raw") or {}).get("tool_input")
            if tool_input:
                existing_inputs.add(tool_input.strip())

        session_exists = kv.get(KV.sessions, session_id) is not None
        if not session_exists:
            session = {
                "id": session_id,
                "project": project_path,
                "cwd": project_path,
                "startedAt": datetime_now_iso(),
                "status": "active",
                "observationCount": 0,
                "summary": f"Antigravity Pair Programming ({convo_id[:8]})",
                "firstPrompt": f"Antigravity Pair Programming ({convo_id[:8]})",
                "agentId": "antigravity"
            }
            functions.create_session(kv, session)

        convo_synced = False
        for turn in turns:
            prompt = turn["prompt"]
            if prompt.strip() in existing_inputs:
                continue

            payload = {
                "sessionId": session_id,
                "project": project_path,
                "cwd": project_path,
                "hookType": "post_tool_use",
                "timestamp": turn["timestamp"],
                "agentId": "antigravity",
                "data": {
                    "tool_name": "conversation",
                    "tool_input": prompt,
                    "tool_output": turn["response"],
                }
            }
            functions.observe(kv, payload)
            observations_added += 1
            convo_synced = True

        if convo_synced:
            synced_sessions.append(convo_id)

    return {"success": True, "syncedSessions": synced_sessions, "observationsAdded": observations_added}

@app.route("/agentmemory/mcp/tools", methods=["GET"])
def mcp_tools_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    tools = [
        {
            "name": "memory_recall",
            "description": "Search past session observations for relevant context. Use when you need to recall what happened in previous sessions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query keywords"},
                    "limit": {"type": "number", "description": "Max results to return (default 10)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_save",
            "description": "Explicitly save an important insight, decision, or pattern to long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The insight or decision to remember"},
                    "type": {"type": "string", "description": "Memory type: pattern, preference, architecture, bug, workflow, or fact"},
                    "concepts": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated key concepts"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of key concepts"}
                        ]
                    },
                    "files": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated relevant file paths"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of relevant file paths"}
                        ]
                    },
                    "project": {"type": "string", "description": "Canonical project identifier"}
                },
                "required": ["content"]
            }
        },
        {
            "name": "memory_sessions",
            "description": "List recent sessions with their status and observation counts.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_sessions_list",
            "description": "Retrieve list of all memory sessions.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_smart_search",
            "description": "Hybrid semantic+keyword search with progressive disclosure.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "number", "description": "Max results (default 10)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_timeline",
            "description": "Chronological observations around an anchor point.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "anchor": {"type": "string", "description": "Anchor point date or keyword"},
                    "project": {"type": "string", "description": "Filter by project path"},
                    "sessionId": {"type": "string", "description": "Filter by session ID"}
                },
                "required": ["anchor"]
            }
        },
        {
            "name": "memory_observations",
            "description": "Retrieve all observations for a given session ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string", "description": "Session ID to fetch observations for"}
                },
                "required": ["sessionId"]
            }
        },
        {
            "name": "memory_profile",
            "description": "User/project profile with top concepts and file patterns.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project path"}
                },
                "required": ["project"]
            }
        },
        {
            "name": "memory_lessons",
            "description": "List all saved lessons, optionally filtered by project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Filter by project (optional)"},
                    "minConfidence": {"type": "number", "description": "Filter by minimum confidence (optional, default 0.0)"},
                    "limit": {"type": "number", "description": "Max results to return (optional, default 50)"}
                }
            }
        },
        {
            "name": "memory_lesson_save",
            "description": "Save a lesson learned from this session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The lesson learned"},
                    "context": {"type": "string", "description": "When/where this lesson applies"},
                    "project": {"type": "string", "description": "Project this lesson is about"}
                },
                "required": ["content"]
            }
        },
        {
            "name": "memory_lesson_recall",
            "description": "Search lessons by query. Returns lessons sorted by confidence and recency.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "project": {"type": "string", "description": "Filter by project"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_lesson_search",
            "description": "Search lessons learned by query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query keywords"},
                    "project": {"type": "string", "description": "Filter by project path (optional)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_consolidate",
            "description": "Trigger the consolidation pipeline to summarize sessions and extract semantic/procedural memory.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_reflect",
            "description": "Trigger reflection for a session, updating pending items, project context, and session patterns.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string", "description": "Session ID to reflect upon"},
                    "maxObservations": {"type": "number", "description": "Max observations to scan (optional, default 50)"}
                },
                "required": ["sessionId"]
            }
        },
        {
            "name": "memory_diagnose",
            "description": "Run diagnostic health checks across all memory subsystems.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_forget",
            "description": "Delete a memory, a session, or specific observations within a session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memoryId": {"type": "string", "description": "Memory ID to delete"},
                    "sessionId": {"type": "string", "description": "Session ID to delete"},
                    "observationIds": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated observation IDs to delete"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of observation IDs to delete"}
                        ]
                    }
                }
            }
        },
        {
            "name": "memory_export",
            "description": "Export all memory data including sessions, memories, lessons, observations, and slots as JSON.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "maxSessions": {"type": "number", "description": "Max sessions to export (optional)"},
                    "offset": {"type": "number", "description": "Pagination offset for sessions (optional)"}
                }
            }
        },
        {
            "name": "agent_observe",
            "description": "Log a direct observation, thought, command execution, or action from the agent's active execution.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agentId": {"type": "string", "description": "ID/Name of the agent logging this (e.g. 'antigravity', optional)"},
                    "sessionId": {"type": "string", "description": "Active session ID"},
                    "project": {"type": "string", "description": "Canonical project path/identifier"},
                    "text": {"type": "string", "description": "The observation log, thought, or content"},
                    "content": {"type": "string", "description": "The observation log, thought, or content (alternative to text)"},
                    "type": {"type": "string", "description": "Observation type: thought, command, tool, error, result, conversation, other"},
                    "title": {"type": "string", "description": "A short summary title for the observation"},
                    "cwd": {"type": "string", "description": "Current working directory"}
                },
                "required": ["sessionId", "project"]
            }
        },
        {
            "name": "agent_remember",
            "description": "Explicitly save a key insight, fact, user preference, or architecture decision to long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agentId": {"type": "string", "description": "ID/Name of the agent (e.g. 'antigravity', optional)"},
                    "content": {"type": "string", "description": "The memory content/insight"},
                    "project": {"type": "string", "description": "Canonical project path/identifier"},
                    "type": {"type": "string", "description": "Memory type: fact, preference, bug, workflow, architecture"},
                    "concepts": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated key concepts"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of key concepts"}
                        ]
                    },
                    "files": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated relevant file paths"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of relevant file paths"}
                        ]
                    }
                },
                "required": ["content", "project"]
            }
        },
        {
            "name": "memory_antigravity_sync",
            "description": "Sync Antigravity chat transcripts to agentmemory. Supports syncing the current session, all sessions, or sessions associated with the current folder.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "description": "Sync mode: current_session (default), current_folder, or all"},
                    "currentConversationId": {"type": "string", "description": "Optional conversation ID of the current active session"},
                    "currentFolder": {"type": "string", "description": "Optional current folder path to filter by"}
                },
                "required": ["mode"]
            }
        },
        {
            "name": "memory_antigravity_sync_all",
            "description": "Sync the current Antigravity session, automatically crystallize (summarize) it, and reflect to populate pinned memory slots in a single action.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "description": "Sync mode: current_session (default), current_folder, or all"},
                    "currentConversationId": {"type": "string", "description": "Optional conversation ID of the current active session"},
                    "currentFolder": {"type": "string", "description": "Optional current folder path to filter by"}
                },
                "required": ["mode"]
            }
        },
        {
            "name": "memory_slot_list",
            "description": "List all pinned memory slots.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_slot_get",
            "description": "Retrieve the content of a specific pinned memory slot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The label of the pinned slot to fetch"}
                },
                "required": ["label"]
            }
        },
        {
            "name": "memory_slot_create",
            "description": "Create a new pinned memory slot or overwrite an existing one.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The label of the pinned slot"},
                    "content": {"type": "string", "description": "Initial content for the slot (optional)"},
                    "scope": {"type": "string", "description": "Scope: global or session (optional, default 'global')"},
                    "sizeLimit": {"type": "number", "description": "Character limit (optional)"},
                    "pinned": {"type": "boolean", "description": "Whether pinned to context (optional, default true)"}
                },
                "required": ["label"]
            }
        },
        {
            "name": "memory_slot_append",
            "description": "Append text content to a pinned memory slot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The label of the pinned slot"},
                    "text": {"type": "string", "description": "Text to append"}
                },
                "required": ["label", "text"]
            }
        },
        {
            "name": "memory_slot_replace",
            "description": "Replace the content of a pinned memory slot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The label of the pinned slot"},
                    "content": {"type": "string", "description": "New content"}
                },
                "required": ["label", "content"]
            }
        },
        {
            "name": "memory_slot_delete",
            "description": "Delete a pinned memory slot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The label of the pinned slot to delete"}
                },
                "required": ["label"]
            }
        },
        {
            "name": "memory_action_create",
            "description": "Create a new work item / action.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the action"},
                    "description": {"type": "string", "description": "Detailed description of the action (optional)"},
                    "priority": {"type": "number", "description": "Priority score, higher is more urgent (optional, default 0)"},
                    "status": {"type": "string", "description": "Status: pending, active, completed (optional, default 'pending')"},
                    "tags": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated tags (optional)"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of tags (optional)"}
                        ]
                    },
                    "sessionId": {"type": "string", "description": "Link to a specific session ID (optional)"}
                },
                "required": ["title"]
            }
        },
        {
            "name": "memory_action_update",
            "description": "Update fields of an existing action.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "actionId": {"type": "string", "description": "ID of the action to update"},
                    "title": {"type": "string", "description": "Updated title (optional)"},
                    "description": {"type": "string", "description": "Updated description (optional)"},
                    "priority": {"type": "number", "description": "Updated priority (optional)"},
                    "status": {"type": "string", "description": "Updated status: pending, active, completed, discarded (optional)"},
                    "tags": {
                        "oneOf": [
                            {"type": "string", "description": "Comma-separated tags (optional)"},
                            {"type": "array", "items": {"type": "string"}, "description": "List of tags (optional)"}
                        ]
                    },
                    "sessionId": {"type": "string", "description": "Updated session ID (optional)"}
                },
                "required": ["actionId"]
            }
        },
        {
            "name": "memory_frontier",
            "description": "Get pending and active actions sorted by priority.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "memory_crystallize",
            "description": "Crystallize/summarize all observations in a session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string", "description": "Session ID to crystallize"}
                },
                "required": ["sessionId"]
            }
        }
    ]
    return jsonify({"tools": tools}), 200


@app.route("/agentmemory/mcp/tools", methods=["POST"])
def mcp_tools_call():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    try:
        body = request.get_json(force=True) or {}
        name = body.get("name")
        args = body.get("arguments") or {}
        if not name:
            return jsonify({"error": "name is required"}), 400
            
        print(f"[mcp] Calling tool {name} with args: {args}")
        text_out = ""
        
        if name == "memory_recall":
            q = args.get("query")
            limit = int(args.get("limit") or 10)
            res = functions._hybrid_search.search(q, limit)
            res = enrich_search_results(kv, res)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_save":
            content = args.get("content")
            concepts = parse_mcp_list_arg(args.get("concepts"))
            files = parse_mcp_list_arg(args.get("files"))
            session_id = args.get("sessionId")
            project = args.get("project")
            res = functions.remember(kv, {
                "content": content,
                "type": args.get("type") or "fact",
                "concepts": concepts,
                "files": files,
                "project": project
            })
            # If sessionId provided, also write observation so memory is linked to session
            if session_id and project and content:
                obs_payload = {
                    "sessionId": session_id,
                    "project": project,
                    "cwd": "",
                    "hookType": "post_tool_use",
                    "timestamp": datetime_now_iso(),
                    "agentId": functions.get_agent_id() or "agent",
                    "data": {
                        "tool_name": "memory_save",
                        "tool_input": content[:500],
                        "tool_output": res.get("id", ""),
                    }
                }
                functions.observe(kv, obs_payload)
            text_out = json.dumps(res)
            
        elif name in ("memory_sessions", "memory_sessions_list"):
            sessions = functions.list_sessions(kv)
            text_out = json.dumps({"sessions": sessions}, indent=2)
            
        elif name == "memory_smart_search":
            q = args.get("query")
            limit = int(args.get("limit") or 10)
            res = functions._hybrid_search.search(q, limit)
            res = enrich_search_results(kv, res)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_timeline":
            res = functions.timeline(kv, {
                "anchor": args.get("anchor"),
                "project": args.get("project"),
                "sessionId": args.get("sessionId")
            })
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_observations":
            session_id = args.get("sessionId")
            if not session_id:
                return jsonify({"error": "sessionId is required"}), 400
            obs = kv.list(KV.observations(session_id))
            obs.sort(key=lambda o: o.get("timestamp", ""))
            text_out = json.dumps({"observations": obs}, indent=2)

        elif name == "memory_profile":
            res = functions.build_project_profile(kv, args.get("project"))
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_lessons":
            res = functions.lesson_list(kv, {
                "project": args.get("project"),
                "minConfidence": args.get("minConfidence"),
                "limit": args.get("limit")
            })
            text_out = json.dumps(res, indent=2)

        elif name == "memory_lesson_save":
            res = functions.lesson_save(kv, {
                "content": args.get("content"),
                "context": args.get("context"),
                "project": args.get("project")
            })
            text_out = json.dumps(res)
            
        elif name in ("memory_lesson_recall", "memory_lesson_search"):
            res = functions.lesson_recall(kv, {
                "query": args.get("query"),
                "project": args.get("project")
            })
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_consolidate":
            res = functions.consolidate(kv)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_reflect":
            session_id = args.get("sessionId")
            max_obs = int(args.get("maxObservations") or 50)
            if not session_id:
                return jsonify({"error": "sessionId is required"}), 400
            res = functions.slot_reflect(kv, session_id, max_obs)
            text_out = json.dumps(res, indent=2)

        elif name == "memory_diagnose":
            res = functions.health_check(kv)
            text_out = json.dumps(res, indent=2)

        elif name == "memory_forget":
            obs_ids = parse_mcp_list_arg(args.get("observationIds"))
            res = functions.forget(kv, {
                "memoryId": args.get("memoryId"),
                "sessionId": args.get("sessionId"),
                "observationIds": obs_ids
            })
            text_out = json.dumps(res, indent=2)

        elif name == "memory_export":
            max_sess = args.get("maxSessions")
            offset = args.get("offset")
            payload = {}
            if max_sess is not None: payload["maxSessions"] = max_sess
            if offset is not None: payload["offset"] = offset
            res = functions.export_data(kv, payload)
            text_out = json.dumps(res, indent=2)

        elif name == "agent_observe":
            agent_id = args.get("agentId") or functions.get_agent_id() or "agent"
            session_id = args.get("sessionId")
            project = args.get("project")
            text = args.get("text") or args.get("content")
            obs_type = args.get("type") or "other"
            title = args.get("title") or f"agent_{obs_type}"
            cwd = args.get("cwd") or ""
            
            if not session_id or not project or not text:
                return jsonify({"error": "sessionId, project, and text (or content) are required"}), 400
                
            payload = {
                "sessionId": session_id,
                "project": project,
                "cwd": cwd,
                "hookType": "post_tool_use",
                "timestamp": datetime_now_iso(),
                "agentId": agent_id,
                "data": {
                    "tool_name": title,
                    "tool_input": text,
                    "tool_output": text,
                }
            }
            res = functions.observe(kv, payload)
            text_out = json.dumps(res)

        elif name == "agent_remember":
            agent_id = args.get("agentId") or functions.get_agent_id() or "agent"
            content = args.get("content")
            project = args.get("project")
            session_id = args.get("sessionId")
            mem_type = args.get("type") or "fact"
            concepts = parse_mcp_list_arg(args.get("concepts"))
            files = parse_mcp_list_arg(args.get("files"))

            if not content or not project:
                return jsonify({"error": "content and project are required"}), 400

            payload = {
                "content": content,
                "type": mem_type,
                "concepts": concepts,
                "files": files,
                "project": project,
                "agentId": agent_id
            }
            res = functions.remember(kv, payload)
            # If sessionId provided, write observation to link memory to session
            if session_id and content:
                obs_payload = {
                    "sessionId": session_id,
                    "project": project,
                    "cwd": "",
                    "hookType": "post_tool_use",
                    "timestamp": datetime_now_iso(),
                    "agentId": agent_id,
                    "data": {
                        "tool_name": "agent_remember",
                        "tool_input": content[:500],
                        "tool_output": res.get("id", ""),
                    }
                }
                functions.observe(kv, obs_payload)
            text_out = json.dumps(res)
            
        elif name == "memory_antigravity_sync":
            mode = args.get("mode") or "current_session"
            current_convo = args.get("currentConversationId")
            current_folder = args.get("currentFolder")
            res = perform_antigravity_sync(mode, current_convo, current_folder)
            text_out = json.dumps(res)
            
        elif name == "memory_antigravity_sync_all":
            mode = args.get("mode") or "current_session"
            current_convo = args.get("currentConversationId")
            current_folder = args.get("currentFolder")
            sync_res = perform_antigravity_sync(mode, current_convo, current_folder)
            
            synced_sessions = sync_res.get("syncedSessions") or []
            crystallizations = {}
            reflections = {}
            
            for cid in synced_sessions:
                session_id = f"antigravity_{cid[:18].replace('-', '_')}"
                
                try:
                    cres = functions.summarize(kv, {"sessionId": session_id})
                    crystallizations[session_id] = cres
                except Exception as ex:
                    crystallizations[session_id] = {"success": False, "error": str(ex)}
                    
                try:
                    rres = functions.slot_reflect(kv, session_id, 50)
                    reflections[session_id] = rres
                except Exception as ex:
                    reflections[session_id] = {"success": False, "error": str(ex)}
                    
            text_out = json.dumps({
                "success": sync_res.get("success", True),
                "syncedSessions": synced_sessions,
                "observationsAdded": sync_res.get("observationsAdded", 0),
                "crystallizations": crystallizations,
                "reflections": reflections
            }, indent=2)
            
        elif name == "memory_slot_list":
            res = functions.slot_list(kv)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_slot_get":
            label = args.get("label")
            if not label:
                return jsonify({"error": "label is required"}), 400
            res = functions.slot_get(kv, label)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_slot_create":
            label = args.get("label")
            if not label:
                return jsonify({"error": "label is required"}), 400
            res = functions.slot_create(kv, {
                "label": label,
                "content": args.get("content"),
                "scope": args.get("scope") or "global",
                "sizeLimit": args.get("sizeLimit"),
                "pinned": args.get("pinned", True)
            })
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_slot_append":
            label = args.get("label")
            text = args.get("text")
            if not label or not text:
                return jsonify({"error": "label and text are required"}), 400
            res = functions.slot_append(kv, label, text)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_slot_replace":
            label = args.get("label")
            content = args.get("content")
            if not label or content is None:
                return jsonify({"error": "label and content are required"}), 400
            res = functions.slot_replace(kv, label, content)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_slot_delete":
            label = args.get("label")
            if not label:
                return jsonify({"error": "label is required"}), 400
            res = functions.slot_delete(kv, label)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_action_create":
            action_id = functions.generate_id("act")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            tags = parse_mcp_list_arg(args.get("tags"))
            action = {
                "id": action_id,
                "title": args.get("title") or "",
                "description": args.get("description"),
                "priority": args.get("priority", 0),
                "status": args.get("status", "pending"),
                "tags": tags,
                "sessionId": args.get("sessionId"),
                "createdAt": now,
                "updatedAt": now,
            }
            kv.set(KV.actions, action_id, action)
            text_out = json.dumps({"action": action, "success": True}, indent=2)
            
        elif name == "memory_action_update":
            action_id = args.get("actionId")
            if not action_id:
                return jsonify({"error": "actionId is required"}), 400
            existing = kv.get(KV.actions, action_id)
            if not existing:
                return jsonify({"error": "action not found"}), 404
            from datetime import datetime, timezone
            allowed_fields = {"title", "description", "priority", "status", "tags", "sessionId"}
            updates = {k: v for k, v in args.items() if k in allowed_fields}
            if "tags" in args:
                updates["tags"] = parse_mcp_list_arg(args.get("tags"))
            existing.update(updates)
            existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
            kv.set(KV.actions, action_id, existing)
            text_out = json.dumps({"action": existing, "success": True}, indent=2)
            
        elif name == "memory_frontier":
            items = kv.list(KV.actions)
            frontier = [a for a in items if a.get("status") in ("pending", "active")]
            frontier.sort(key=lambda a: (-(a.get("priority") or 0), a.get("createdAt", "")))
            text_out = json.dumps({"frontier": frontier[:50], "total": len(frontier)}, indent=2)
            
        elif name == "memory_crystallize":
            session_id = args.get("sessionId")
            if not session_id:
                return jsonify({"error": "sessionId is required"}), 400
            res = functions.summarize(kv, {"sessionId": session_id})
            text_out = json.dumps(res, indent=2)
            
        else:
            return jsonify({"error": f"unknown tool: {name}"}), 400
            
        return jsonify({
            "content": [
                {"type": "text", "text": text_out}
            ]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =====================================================================
# Agent Discovery Endpoint  (/agent.md  and  /auth.md)
# =====================================================================

AGENT_MD_TEMPLATE = """# AgentMemory — Agent Connection Guide

> This document is auto-generated. Fetch it fresh each session so you always have current values.

## 1. Server

| Field | Value |
|-------|-------|
| Base URL | `http://127.0.0.1:{port}` |
| API prefix | `/agentmemory` |
| Liveness | `GET http://127.0.0.1:{port}/agentmemory/livez` (no auth) |
| Auth required | `{auth_required}` |
| Auth header | `Authorization: Bearer <AGENTMEMORY_SECRET>` |

{auth_note}

---

## 2. Quick-Start: Minimum Viable Session

Do these in order at the start of every session:

```
1. POST /agentmemory/session/start          ← register yourself
2. POST /agentmemory/context                ← inject past memory into your system prompt
   ... do work, call observe after each tool use ...
3. POST /agentmemory/session/end            ← mark session complete
```

### 2a. Start a session

```http
POST /agentmemory/session/start
Content-Type: application/json
{auth_header_example}

{{
  "sessionId": "<uuid-you-generate>",
  "project":   "<absolute-path-or-name-of-repo>",
  "cwd":       "<current working directory>",
  "agentId":   "<your-agent-name>",
  "title":     "<first user prompt, optional>"
}}
```

Response includes `context` — paste it into your system prompt before answering.

### 2b. Compile context (also works mid-session)

```http
POST /agentmemory/context
Content-Type: application/json
{auth_header_example}

{{
  "sessionId": "<your-session-id>",
  "project":   "<same project as above>",
  "budget":    2000
}}
```

Returns `context` string wrapped in `<agentmemory-context>` tags. Insert verbatim.

### 2c. Log an observation (call after every significant tool use)

```http
POST /agentmemory/agent/observe
Content-Type: application/json
{auth_header_example}

{{
  "agentId":   "<your-agent-name>",
  "sessionId": "<your-session-id>",
  "project":   "<project>",
  "cwd":       "<cwd>",
  "text":      "<what you did or observed>",
  "type":      "tool | command | thought | error | result | conversation | other",
  "title":     "<short one-line summary>"
}}
```

### 2d. Save a long-term memory (decisions, patterns, preferences)

```http
POST /agentmemory/agent/remember
Content-Type: application/json
{auth_header_example}

{{
  "agentId":  "<your-agent-name>",
  "project":  "<project>",
  "content":  "<the insight, decision, or fact>",
  "type":     "fact | preference | architecture | bug | workflow | pattern",
  "concepts": "auth, jwt, middleware",
  "files":    "src/middleware/auth.ts, tests/auth.test.ts"
}}
```

### 2e. End session

```http
POST /agentmemory/session/end
Content-Type: application/json
{auth_header_example}

{{
  "sessionId": "<your-session-id>"
}}
```

---

## 3. MCP Tools (JSON-RPC style)

Call via: `POST /agentmemory/mcp/tools`

```http
POST /agentmemory/mcp/tools
Content-Type: application/json
{auth_header_example}

{{
  "name":      "<tool-name>",
  "arguments": {{ ... }}
}}
```

| Tool | Required args | Description |
|------|---------------|-------------|
| `memory_recall` | `query` | BM25+vector hybrid search over all past observations |
| `memory_smart_search` | `query` | Same as recall with progressive disclosure |
| `memory_save` | `content` | Save long-term memory (type, concepts, files optional) |
| `memory_sessions` | — | List recent sessions with observation counts |
| `memory_timeline` | `anchor` | Observations around a date or keyword anchor |
| `memory_profile` | `project` | Top concepts, files, conventions for a project |
| `memory_lesson_save` | `content` | Save a lesson (auto-strengthens duplicates) |
| `memory_lesson_recall` | `query` | Search lessons scored by confidence + recency |
| `agent_observe` | `agentId`, `sessionId`, `project`, `text` | Log an observation (same as REST) |
| `agent_remember` | `agentId`, `content`, `project` | Save memory (same as REST) |

Get full schemas: `GET /agentmemory/mcp/tools`

---

## 4. Full REST Endpoint Reference

All endpoints require `Authorization: Bearer <secret>` when auth is enabled.
Prefix every path with `http://127.0.0.1:{port}`.

### Session Management

| Method | Path | Key body fields | Notes |
|--------|------|-----------------|-------|
| `POST` | `/agentmemory/session/start` | `sessionId`, `project`, `cwd`, `agentId` | Returns `session` + `context` |
| `POST` | `/agentmemory/session/end` | `sessionId` | Marks status=completed |
| `GET`  | `/agentmemory/sessions` | — | List all sessions |
| `GET`  | `/agentmemory/observations?sessionId=` | — | Observations for a session |
| `POST` | `/agentmemory/session/commit` | `sha`, `sessionId`, `branch`, `message` | Link a git commit to a session |
| `GET`  | `/agentmemory/session/by-commit?sha=` | — | Sessions linked to a commit |
| `GET`  | `/agentmemory/commits` | `branch`, `repo`, `limit` | List tracked commits |

### Observations

| Method | Path | Key body fields | Notes |
|--------|------|-----------------|-------|
| `POST` | `/agentmemory/observe` | `sessionId`, `hookType`, `timestamp`, `data` | Raw hook payload |
| `POST` | `/agentmemory/agent/observe` | `agentId`, `sessionId`, `project`, `text`, `type` | Simplified agent endpoint |

`hookType` values: `post_tool_use`, `post_tool_failure`, `prompt_submit`, `subagent_stop`, `task_completed`, `notification`

### Memory

| Method | Path | Key body fields | Notes |
|--------|------|-----------------|-------|
| `POST` | `/agentmemory/remember` | `content`, `type`, `concepts[]`, `files[]`, `project` | Supersedes similar memories automatically |
| `POST` | `/agentmemory/agent/remember` | `agentId`, `content`, `project`, `type`, `concepts`, `files` | Simplified agent endpoint |
| `POST` | `/agentmemory/forget` | `memoryId` OR `sessionId` [+ `observationIds[]`] | Delete memory/session/observations |
| `POST` | `/agentmemory/evolve` | `memoryId`, `newContent`, `newTitle` | Version a memory (creates new, marks old superseded) |
| `POST` | `/agentmemory/search` | `query`, `limit` | Hybrid BM25+vector search |
| `POST` | `/agentmemory/context` | `sessionId`, `project`, `budget` | Compile context block for injection |

Memory `type` values: `fact`, `preference`, `architecture`, `bug`, `workflow`, `pattern`

### Lessons

| Method | Path | Key body fields | Notes |
|--------|------|-----------------|-------|
| `POST` | `/agentmemory/lessons` | `content`, `context`, `project`, `confidence`, `tags[]` | Create/strengthen (duplicate = auto-reinforce) |
| `GET`  | `/agentmemory/lessons` | `project`, `minConfidence`, `limit` | List lessons |
| `POST` | `/agentmemory/lessons/search` | `query`, `project`, `limit` | Keyword search scored by confidence×recency |
| `POST` | `/agentmemory/lessons/strengthen` | `lessonId` | Manually reinforce a lesson (+0.1 confidence) |

### Memory Slots (Pinned Context)

Slots are named text buffers injected into every context compilation.

| Method | Path | Key body/query fields | Notes |
|--------|------|-----------------------|-------|
| `GET`  | `/agentmemory/slots` | — | List all slots |
| `GET`  | `/agentmemory/slot?label=` | — | Get single slot |
| `POST` | `/agentmemory/slot` | `label`, `content`, `scope`, `sizeLimit`, `pinned` | Create slot |
| `POST` | `/agentmemory/slot/append` | `label`, `text` | Append text to slot |
| `POST` | `/agentmemory/slot/replace` | `label`, `content` | Replace slot content |
| `DELETE` | `/agentmemory/slot?label=` | — | Delete slot |
| `POST` | `/agentmemory/slot/reflect` | `sessionId`, `maxObservations` | Auto-populate slots from session observations |

Default slot labels: `persona`, `user_preferences`, `tool_guidelines`, `project_context`, `guidance`, `pending_items`, `session_patterns`, `self_notes`

### Knowledge Graph & Analytics

| Method | Path | Key body/query fields | Notes |
|--------|------|-----------------------|-------|
| `GET`  | `/agentmemory/relations` | — | List all knowledge graph edges |
| `POST` | `/agentmemory/relations` | `sourceId`, `targetId`, `type` | Add a relation edge |
| `POST` | `/agentmemory/timeline` | `anchor`, `project`, `before`, `after` | Observations around anchor |
| `GET`  | `/agentmemory/profile?project=` | — | Project profile (top concepts/files) |
| `GET`  | `/agentmemory/audit` | `operation`, `limit` | Audit log |
| `GET`  | `/agentmemory/profile` | `project` (optional) | Project profile; omit `project` to list all known projects |
| `GET`  | `/agentmemory/actions` | `limit`, `status` | List actions |
| `POST` | `/agentmemory/actions` | `title`, `description`, `priority`, `tags[]`, `status` | Create action |
| `PATCH`| `/agentmemory/actions/<id>` | any action fields | Update action |
| `GET`  | `/agentmemory/frontier` | — | Pending+active actions sorted by priority |
| `GET`  | `/agentmemory/insights` | `limit` | List insights |
| `GET`  | `/agentmemory/replay/sessions` | — | Sessions list for replay |
| `GET`  | `/agentmemory/replay/load?sessionId=` | — | Full session + ordered observations for replay |
| `POST` | `/agentmemory/auto-forget` | `dryRun` | Evict stale observations |

### Health & Config

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/agentmemory/livez` | No | Liveness check |
| `GET`  | `/agentmemory/health` | Yes | Full health + version |
| `GET`  | `/agentmemory/config/flags` | Yes | Feature flags (graph, consolidation, compression) |

---

## 5. WebSocket Live Stream

```
ws://127.0.0.1:{port}/stream/mem-live/viewer
```

Connect to receive real-time events. Message types:
- `raw_observation` — raw hook payload as received
- `compressed_observation` — after synthetic compression + indexing

No auth on the WebSocket endpoint.

---

## 6. Agent Identity & Scope Isolation

Set `AGENT_ID` env var on the server to scope all read operations to a single agent.
Set `AGENTMEMORY_AGENT_SCOPE=isolated` to enforce per-agent isolation.

Always pass `agentId` in every request body — it's stored on every observation/memory and enables per-agent filtering via `?agentId=<id>` on list endpoints.

---

## 7. Secrets Are Redacted

Any text containing API keys, Bearer tokens, passwords, or JWTs is automatically redacted to `[REDACTED_SECRET]` before storage. Wrap sensitive blocks in `<private>...</private>` tags to force redaction.

---

*Generated by agentmemory v{version} — `GET /agent.md` to refresh*
"""

@app.route("/agent.md", methods=["GET"])
@app.route("/auth.md", methods=["GET"])
def agent_discovery():
    auth_err = check_auth()
    if auth_err:
        return auth_err
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    secret = os.getenv("AGENTMEMORY_SECRET")
    auth_required = "yes" if secret else "no"

    if secret:
        auth_note = (
            "> **Auth is enabled.** Add `Authorization: Bearer <AGENTMEMORY_SECRET>` to every request.\n"
            "> The secret is set via `AGENTMEMORY_SECRET` env var in `~/.agentmemory/.env`."
        )
        auth_header_example = 'Authorization: Bearer <AGENTMEMORY_SECRET>'
    else:
        auth_note = (
            "> **Auth is disabled.** No `Authorization` header needed — all endpoints are open."
        )
        auth_header_example = ''

    md = AGENT_MD_TEMPLATE.format(
        port=port,
        auth_required=auth_required,
        auth_note=auth_note,
        auth_header_example=auth_header_example,
        version="0.9.8",
    )

    response = make_response(md)
    response.headers["Content-Type"] = "text/markdown; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    return response

# =====================================================================
# Lifecycle Helpers
# =====================================================================

if __name__ == "__main__":
    init_app()
    # Default Flask server settings
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    # Listen on all interfaces (required for container deployments like HF Spaces)
    print(f"[main] Starting Flask daemon on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
