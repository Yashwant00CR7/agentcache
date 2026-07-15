"""
agentmemory-python — Flask application factory.

Entry point: create_app() returns a fully configured Flask app.
Run directly:  python src/app.py
"""

import hmac
import json
import os
import sys

from flask import Flask, request, send_from_directory
from flask_sock import Sock

from . import functions

# Prevent double-import of app when run directly as __main__
if __name__ == "__main__":
    sys.modules["app"] = sys.modules["__main__"]


def _load_env() -> None:
    env_path = os.path.join(os.path.expanduser("~"), ".agentcache", ".env")
    if not os.path.exists(env_path):
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


def init_services() -> tuple:
    """Initialise database, embedding provider, and index persistence."""
    global kv, embedding_provider, persistence
    if kv is not None:
        return kv, embedding_provider, persistence

    from . import functions
    from . import search as search_mod
    from .db import StateKV

    # 1. DB
    kv = StateKV()

    # 2. Embedding provider — auto-select by priority (D5.3):
    #    GEMINI_API_KEY → OPENAI_API_KEY → AGENTCACHE_LOCAL_EMBEDDING_MODEL → BM25-only
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    local_model = os.getenv("AGENTCACHE_LOCAL_EMBEDDING_MODEL") or os.getenv(
        "AGENTMEMORY_LOCAL_EMBEDDING_MODEL"
    )

    if api_key:
        try:
            embedding_provider = search_mod.GeminiEmbeddingProvider(api_key)
            functions.set_embedding_provider(embedding_provider)
            print(
                f"[search] Embedding provider active: gemini ({embedding_provider.dimensions} dims)"
            )
        except Exception as e:
            print(f"[search] Error initialising Gemini embedding provider: {e}")
    elif openai_key:
        try:
            embedding_provider = search_mod.OpenAIEmbeddingProvider(openai_key)
            functions.set_embedding_provider(embedding_provider)
            print(
                f"[search] Embedding provider active: openai ({embedding_provider.dimensions} dims)"
            )
        except Exception as e:
            print(f"[search] Error initialising OpenAI embedding provider: {e}")
    elif local_model:
        try:
            embedding_provider = search_mod.SentenceTransformerProvider(local_model)
            functions.set_embedding_provider(embedding_provider)
            print(
                f"[search] Embedding provider active: sentence-transformers/{local_model} ({embedding_provider.dimensions} dims)"
            )
        except ImportError as e:
            print(f"[search] sentence-transformers not installed: {e}")
        except Exception as e:
            print(f"[search] Error initialising SentenceTransformer provider: {e}")
    else:
        print("[search] No embedding API key found — running in BM25-only mode.")

    # 3. Index persistence — use embedding_provider variable set above
    has_vector = embedding_provider is not None
    persistence = functions.IndexPersistence(
        kv,
        functions._bm25_index,
        functions._vector_index if has_vector else None,
    )
    functions.set_index_persistence(persistence)
    loaded = persistence.load()
    print(
        f"[persistence] Load results: BM25={loaded['bm25']}, Vector={loaded['vector']}"
    )

    # Backfill coordinate lookup index if missing/incomplete
    try:
        functions.backfill_obs_lookup_if_needed(kv)
    except Exception as e:
        print(f"[db] Warning backfilling obs_lookup: {e}")

    return kv, embedding_provider, persistence


def create_app() -> Flask:
    """Create and return a fully configured Flask application."""
    # Check security credentials
    if not os.getenv("AGENTCACHE_SECRET") and not os.getenv("AGENTMEMORY_SECRET"):
        print(
            "[security] WARNING: AGENTCACHE_SECRET/AGENTMEMORY_SECRET is not set! All API endpoints are publicly accessible without authentication."
        )

    init_services()

    from .viewer_helpers import make_viewer_response

    # 4. Flask app + blueprints
    flask_app = Flask(__name__)
    from werkzeug.middleware.proxy_fix import ProxyFix

    flask_app.wsgi_app = ProxyFix(
        flask_app.wsgi_app, x_proto=1, x_host=1, x_port=1, x_prefix=1
    )
    from .routes import register_blueprints

    register_blueprints(flask_app)

    # 5. WebSocket broadcaster
    sock = Sock(flask_app)
    _ws_clients: set = set()

    @sock.route("/stream/mem-live/viewer")
    def stream_viewer(ws):
        secret = os.getenv("AGENTCACHE_SECRET") or os.getenv("AGENTMEMORY_SECRET")
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
    from importlib.resources import files

    _viewer_resources = files("agentcache").joinpath("viewer")
    _base_dir = str(_viewer_resources.parent)

    @flask_app.route("/")
    @flask_app.route("/viewer")
    @flask_app.route("/agentcache/viewer")
    @flask_app.route("/agentmemory/viewer")
    def serve_viewer():
        try:
            return make_viewer_response(_base_dir)
        except Exception as e:
            return f"Viewer not found: {e}", 404

    @flask_app.route("/favicon.svg")
    def serve_favicon():
        return send_from_directory(str(_viewer_resources), "favicon.svg")

    # 7. CORS after_request — D2.1: configurable via AGENTCACHE_CORS_ORIGINS env var
    # Default allows localhost, 127.0.0.1, HuggingFace Spaces, vscode-webview://, chrome-extension://
    # Wildcard entries like "*.hf.space" match any subdomain via suffix check.
    _default_cors = (
        "http://localhost,http://127.0.0.1,"
        "https://huggingface.co,https://*.hf.space,"
        "vscode-webview://*,chrome-extension://*"
    )
    _cors_origins_raw = (
        os.getenv("AGENTCACHE_CORS_ORIGINS")
        or os.getenv("AGENTMEMORY_CORS_ORIGINS")
        or _default_cors
    )

    def _parse_cors_origins(raw: str):
        """Return (exact_set, suffix_list) for efficient origin matching."""
        exact, suffixes = set(), []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if entry.startswith("*."):
                # *.hf.space → match anything ending with .hf.space
                suffixes.append(entry[1:].lower())  # keep the leading dot: ".hf.space"
            elif "*" in entry:
                # generic prefix wildcard: strip trailing * and treat as prefix
                suffixes.append(("prefix:", entry.rstrip("*").lower()))
            else:
                exact.add(entry.lower())
        return exact, suffixes

    _cors_exact, _cors_suffixes = _parse_cors_origins(_cors_origins_raw)

    def _origin_allowed(origin: str) -> bool:
        lo = origin.lower()
        if lo in _cors_exact:
            return True
        for s in _cors_suffixes:
            if isinstance(s, tuple) and s[0] == "prefix:":
                if lo.startswith(s[1]):
                    return True
            elif lo.endswith(s):
                return True
        return False

    @flask_app.after_request
    def _cors(response):
        origin = request.headers.get("Origin")
        if origin and _origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers.add(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        response.headers.add(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        return response

    # Handle CORS preflight OPTIONS requests globally
    from flask import Response as _FlaskResponse

    @flask_app.before_request
    def _handle_options():
        if request.method == "OPTIONS":
            origin = request.headers.get("Origin", "")
            if origin and _origin_allowed(origin):
                resp = _FlaskResponse("", status=204)
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
                resp.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization"
                )
                resp.headers["Access-Control-Allow-Methods"] = (
                    "GET, POST, PUT, DELETE, OPTIONS"
                )
                resp.headers["Access-Control-Max-Age"] = "86400"
                return resp

    # 8. Background workers
    if os.getenv("AGENTCACHE_DISABLE_WORKERS") != "true":
        from .workers import start_background_workers

        start_background_workers(kv)

    return flask_app


def main() -> None:
    flask_app = create_app()
    port = int(os.getenv("III_REST_PORT", os.getenv("PORT", "3111")))
    print(f"[main] Starting Flask daemon on port {port}...")
    flask_app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
