"""
agentmemory-python — Flask application factory.

Entry point: create_app() returns a fully configured Flask app.
Run directly:  python src/app.py
"""

import os
import json
import hmac
from flask import Flask, request, make_response, send_from_directory
from flask_sock import Sock


def _load_env() -> None:
    env_path = os.path.join(os.path.expanduser("~"), ".agentmemory", ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
        print(f"[config] Loaded environment from {env_path}")
    except Exception as e:
        print(f"[config] Error reading env file: {e}")


_load_env()

# Module-level globals — set once by create_app(), read by blueprints via `import app`
kv = None
embedding_provider = None
persistence = None


def create_app() -> Flask:
    """Create and return a fully configured Flask application."""
    global kv, embedding_provider, persistence

    import search as search_mod
    import functions
    from db import StateKV
    from viewer_helpers import make_viewer_response

    # 1. DB
    kv = StateKV()

    # 2. Embedding provider — auto-select by priority (D5.3):
    #    GEMINI_API_KEY → OPENAI_API_KEY → AGENTMEMORY_LOCAL_EMBEDDING_MODEL → BM25-only
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    local_model = os.getenv("AGENTMEMORY_LOCAL_EMBEDDING_MODEL")

    if api_key:
        try:
            embedding_provider = search_mod.GeminiEmbeddingProvider(api_key)
            functions.set_embedding_provider(embedding_provider)
            print(f"[search] Embedding provider active: gemini ({embedding_provider.dimensions} dims)")
        except Exception as e:
            print(f"[search] Error initialising Gemini embedding provider: {e}")
    elif openai_key:
        try:
            embedding_provider = search_mod.OpenAIEmbeddingProvider(openai_key)
            functions.set_embedding_provider(embedding_provider)
            print(f"[search] Embedding provider active: openai ({embedding_provider.dimensions} dims)")
        except Exception as e:
            print(f"[search] Error initialising OpenAI embedding provider: {e}")
    elif local_model:
        try:
            embedding_provider = search_mod.SentenceTransformerProvider(local_model)
            functions.set_embedding_provider(embedding_provider)
            print(f"[search] Embedding provider active: sentence-transformers/{local_model} ({embedding_provider.dimensions} dims)")
        except ImportError as e:
            print(f"[search] sentence-transformers not installed: {e}")
        except Exception as e:
            print(f"[search] Error initialising SentenceTransformer provider: {e}")
    else:
        print("[search] No embedding API key found — running in BM25-only mode.")

    # 3. Index persistence — use embedding_provider variable set above
    has_vector = embedding_provider is not None
    persistence = functions.IndexPersistence(
        kv, functions._bm25_index, functions._vector_index if has_vector else None,
    )
    functions.set_index_persistence(persistence)
    loaded = persistence.load()
    print(f"[persistence] Load results: BM25={loaded['bm25']}, Vector={loaded['vector']}")

    # 4. Flask app + blueprints
    flask_app = Flask(__name__)
    from routes import register_blueprints
    register_blueprints(flask_app)

    # 5. WebSocket broadcaster
    sock = Sock(flask_app)
    _ws_clients: set = set()

    @sock.route("/stream/mem-live/viewer")
    def stream_viewer(ws):
        secret = os.getenv("AGENTMEMORY_SECRET")
        if secret:
            token = request.args.get("token") or request.args.get("secret")
            if not token or not hmac.compare_digest(
                token.encode("utf-8"), secret.encode("utf-8")
            ):
                ws.close(1008)
                return
        _ws_clients.add(ws)
        try:
            while ws.receive() is not None:
                pass
        except Exception:
            pass
        finally:
            _ws_clients.discard(ws)

    def _broadcast(payload: dict) -> None:
        msg = json.dumps(payload)
        for ws in list(_ws_clients):
            try:
                ws.send(msg)
            except Exception:
                _ws_clients.discard(ws)

    functions.set_stream_broadcaster(_broadcast)

    # 6. Viewer static routes
    _base_dir = os.path.dirname(os.path.abspath(__file__))

    @flask_app.route("/")
    @flask_app.route("/viewer")
    @flask_app.route("/agentmemory/viewer")
    def serve_viewer():
        try:
            return make_viewer_response(_base_dir)
        except Exception as e:
            return f"Viewer not found: {e}", 404

    @flask_app.route("/favicon.svg")
    def serve_favicon():
        return send_from_directory(os.path.join(_base_dir, "viewer"), "favicon.svg")

    # 7. CORS after_request — D2.1: configurable via AGENTMEMORY_CORS_ORIGINS env var
    # Default allows localhost, 127.0.0.1, vscode-webview://, and chrome-extension://
    _default_cors = "http://localhost,http://127.0.0.1,vscode-webview://,chrome-extension://"
    _cors_origins_raw = os.getenv("AGENTMEMORY_CORS_ORIGINS", _default_cors)
    _allowed_origins = [o.strip().rstrip("*") for o in _cors_origins_raw.split(",") if o.strip()]

    @flask_app.after_request
    def _cors(response):
        origin = request.headers.get("Origin")
        if origin:
            lo = origin.lower()
            if any(lo == allowed.lower() or lo.startswith(allowed.lower())
                   for allowed in _allowed_origins):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers.add("Access-Control-Allow-Headers", "Content-Type, Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        return response

    # 8. Background workers
    from workers import start_background_workers
    start_background_workers(kv)

    return flask_app


def main() -> None:
    flask_app = create_app()
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    print(f"[main] Starting Flask daemon on port {port}...")
    flask_app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
