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
        text = body.get("text") or ""
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
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/agentmemory/replay/sessions", methods=["GET"])
def api_replay_sessions():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    sessions = kv.list(KV.sessions)
    sessions.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
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
        projects = sorted(set(s.get("project", "") for s in sessions if s.get("project")))
        return jsonify({"projects": projects, "success": True}), 200

    res = functions.get_project_profile(kv, project)

    # Stored profile may lack topConcepts/topFiles — compute from observations if empty
    if not res.get("topConcepts") and not res.get("topFiles"):
        import re as _re, json as _j, os.path as _osp
        from collections import Counter
        sessions = kv.list(KV.sessions)
        project_sessions = [s for s in sessions if s.get("project") == project]
        concept_counts = Counter()
        file_counts = Counter()

        def _harvest_file(path, fc, cc):
            if not isinstance(path, str) or not path:
                return
            fc[path] += 1
            # dirname components → concepts
            parts = _re.split(r'[\\/]', path)
            fname = parts[-1] if parts else ""
            # dir components (skip drive letters, temp paths)
            skip = {"tmp", "temp", "claude", "appdata", "local", "users", "windows"}
            for part in parts[:-1]:
                p = part.lower().strip()
                if p and len(p) > 2 and p not in skip and not _re.match(r'^[a-z]:|^\.|^--', p):
                    cc[p] += 1
            # file stem → concept
            stem = _osp.splitext(fname)[0]
            if stem and len(stem) > 2:
                cc[stem.lower()] += 1
            # extension → technology concept
            ext = _osp.splitext(fname)[1].lstrip(".")
            if ext in ("py", "ts", "js", "jsx", "tsx", "go", "rs", "java", "cs", "cpp"):
                cc[ext] += 1

        for s in project_sessions:
            sid = s.get("id", "")
            if not sid:
                continue
            for o in kv.list(KV.observations(sid)):
                # top-level concepts / files
                for c in (o.get("concepts") or []):
                    if isinstance(c, str) and c:
                        concept_counts[c] += 1
                for f in (o.get("files") or []):
                    _harvest_file(f, file_counts, concept_counts)
                # toolName → concept
                tn = o.get("toolName")
                if tn:
                    concept_counts[tn] += 1
                # toolInput path fields
                ti = o.get("toolInput")
                if isinstance(ti, str):
                    try: ti = _j.loads(ti)
                    except Exception: ti = {}
                if isinstance(ti, dict):
                    for fk in ("path", "file_path", "file", "filename"):
                        _harvest_file(ti.get(fk, ""), file_counts, concept_counts)
                # narrative may be JSON string with path/tool info
                narr = o.get("narrative") or o.get("raw") or ""
                if isinstance(narr, str) and narr.startswith("{"):
                    try:
                        nd = _j.loads(narr)
                        if isinstance(nd, dict):
                            tn2 = nd.get("toolName") or nd.get("tool_name")
                            if tn2: concept_counts[tn2] += 1
                            for fk in ("path", "file_path", "file", "filename"):
                                _harvest_file(nd.get(fk, ""), file_counts, concept_counts)
                    except Exception:
                        pass

        # memories for this project
        for m in kv.list(KV.memories):
            if m.get("project") == project:
                for c in (m.get("concepts") or []):
                    if c: concept_counts[c] += 1
                for f in (m.get("files") or []):
                    _harvest_file(f, file_counts, concept_counts)

        res["topConcepts"] = [{"concept": c, "frequency": n} for c, n in concept_counts.most_common(20)]
        res["topFiles"] = [{"file": f, "frequency": n} for f, n in file_counts.most_common(20)]
        res["sessionCount"] = len(project_sessions)

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
    session = kv.get(KV.sessions, session_id)
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
        conn = kv._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT commit_hash, committer, email, date, message FROM dolt_log ORDER BY date DESC LIMIT %s",
                    (limit,)
                )
                rows = cursor.fetchall()
                commits = []
                for r in rows:
                    commits.append({
                        "sha": r["commit_hash"],
                        "shortSha": r["commit_hash"][:7],
                        "agent": r["committer"],
                        "email": r["email"],
                        "date": r["date"].isoformat() + "Z" if isinstance(r["date"], datetime.datetime) else str(r["date"]),
                        "message": r["message"]
                    })
                return jsonify({"success": True, "commits": commits}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/agentmemory/second-brain", methods=["GET"])
def api_get_second_brain():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    brain_dir = os.getenv("SECOND_BRAIN_DIR", os.path.join(os.path.expanduser("~"), ".agentmemory", "second-brain"))
    if not os.path.exists(brain_dir):
        return jsonify({"error": "Second brain directory not found", "path": brain_dir}), 404
        
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

@app.route("/agentmemory/mcp/tools", methods=["GET"])
def mcp_tools_list():
    auth_err = check_auth()
    if auth_err:
        return auth_err
        
    # Return core tools supported by our python daemon
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
                    "concepts": {"type": "string", "description": "Comma-separated key concepts"},
                    "files": {"type": "string", "description": "Comma-separated relevant file paths"},
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
                    "project": {"type": "string", "description": "Filter by project path"}
                },
                "required": ["anchor"]
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
            "name": "agent_observe",
            "description": "Log a direct observation, thought, command execution, or action from the agent's active execution.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agentId": {"type": "string", "description": "ID/Name of the agent logging this (e.g. 'antigravity')"},
                    "sessionId": {"type": "string", "description": "Active session ID"},
                    "project": {"type": "string", "description": "Canonical project path/identifier"},
                    "text": {"type": "string", "description": "The observation log, thought, or content"},
                    "type": {"type": "string", "description": "Observation type: thought, command, tool, error, result, conversation, other"},
                    "title": {"type": "string", "description": "A short summary title for the observation"},
                    "cwd": {"type": "string", "description": "Current working directory"}
                },
                "required": ["agentId", "sessionId", "project", "text"]
            }
        },
        {
            "name": "agent_remember",
            "description": "Explicitly save a key insight, fact, user preference, or architecture decision to long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agentId": {"type": "string", "description": "ID/Name of the agent (e.g. 'antigravity')"},
                    "content": {"type": "string", "description": "The memory content/insight"},
                    "project": {"type": "string", "description": "Canonical project path/identifier"},
                    "type": {"type": "string", "description": "Memory type: fact, preference, bug, workflow, architecture"},
                    "concepts": {"type": "string", "description": "Comma-separated key concepts"},
                    "files": {"type": "string", "description": "Comma-separated relevant file paths"}
                },
                "required": ["agentId", "content", "project"]
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
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_save":
            content = args.get("content")
            concepts = args.get("concepts", "").split(",") if args.get("concepts") else []
            files = args.get("files", "").split(",") if args.get("files") else []
            res = functions.remember(kv, {
                "content": content,
                "type": args.get("type") or "fact",
                "concepts": [c.strip() for c in concepts if c.strip()],
                "files": [f.strip() for f in files if f.strip()],
                "project": args.get("project")
            })
            text_out = json.dumps(res)
            
        elif name == "memory_sessions":
            sessions = functions.list_sessions(kv)
            text_out = json.dumps({"sessions": sessions}, indent=2)
            
        elif name == "memory_smart_search":
            q = args.get("query")
            limit = int(args.get("limit") or 10)
            res = functions._hybrid_search.search(q, limit)
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_timeline":
            res = functions.timeline(kv, {
                "anchor": args.get("anchor"),
                "project": args.get("project")
            })
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_profile":
            res = functions.get_project_profile(kv, args.get("project"))
            text_out = json.dumps(res, indent=2)
            
        elif name == "memory_lesson_save":
            res = functions.lesson_save(kv, {
                "content": args.get("content"),
                "context": args.get("context"),
                "project": args.get("project")
            })
            text_out = json.dumps(res)
            
        elif name == "memory_lesson_recall":
            res = functions.lesson_recall(kv, {
                "query": args.get("query"),
                "project": args.get("project")
            })
            text_out = json.dumps(res, indent=2)

        elif name == "agent_observe":
            agent_id = args.get("agentId")
            session_id = args.get("sessionId")
            project = args.get("project")
            text = args.get("text")
            obs_type = args.get("type") or "other"
            title = args.get("title") or f"agent_{obs_type}"
            cwd = args.get("cwd") or ""
            
            if not agent_id or not session_id or not project or not text:
                return jsonify({"error": "agentId, sessionId, project, and text are required"}), 400
                
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
            agent_id = args.get("agentId")
            content = args.get("content")
            project = args.get("project")
            mem_type = args.get("type") or "fact"
            concepts = args.get("concepts", "").split(",") if args.get("concepts") else []
            files = args.get("files", "").split(",") if args.get("files") else []
            
            if not agent_id or not content or not project:
                return jsonify({"error": "agentId, content, and project are required"}), 400
                
            payload = {
                "content": content,
                "type": mem_type,
                "concepts": [c.strip() for c in concepts if c.strip()],
                "files": [f.strip() for f in files if f.strip()],
                "project": project,
                "agentId": agent_id
            }
            res = functions.remember(kv, payload)
            text_out = json.dumps(res)
            
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
