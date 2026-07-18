"""
src/agentcache/core.py — Object-oriented library wrapper facade (AgentCache & AgentCacheServer).
"""

import contextlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from werkzeug.serving import make_server

from .db import StateKV
from .functions import (
    _local_context,
    folder_graph_build,
    folder_observe,
    folder_search,
    folder_timeline,
    forget,
    health_check,
    remember,
)
from .search import SearchIndex, VectorIndex


@dataclass
class AgentCacheConfig:
    max_obs_per_folder: int = 2000
    token_budget: int = 2000


class AgentCache:
    def __init__(
        self,
        db_path: Optional[str] = None,
        max_obs_per_folder: int = 2000,
        token_budget: int = 2000,
        embedding_provider: Optional[Any] = None,
    ):
        self.kv = StateKV(db_path=db_path) if db_path else StateKV()
        self.bm25_index = SearchIndex()
        self.vector_index = VectorIndex()
        self.embedding_provider = embedding_provider
        self.config = AgentCacheConfig(
            max_obs_per_folder=max_obs_per_folder,
            token_budget=token_budget,
        )

    @contextlib.contextmanager
    def _active_context(self):
        """Binds this instance's indexes and configurations to the thread-local state."""
        prev_bm25 = getattr(_local_context, "bm25_index", None)
        prev_vector = getattr(_local_context, "vector_index", None)
        prev_provider = getattr(_local_context, "embedding_provider", None)
        prev_config = getattr(_local_context, "config", None)

        _local_context.bm25_index = self.bm25_index
        _local_context.vector_index = self.vector_index
        _local_context.embedding_provider = self.embedding_provider
        _local_context.config = self.config
        try:
            yield
        finally:
            _local_context.bm25_index = prev_bm25
            _local_context.vector_index = prev_vector
            _local_context.embedding_provider = prev_provider
            _local_context.config = prev_config

    def observe(self, folder_path: str, agent_id: str, content: str) -> Dict[str, Any]:
        """Log a new observation scoped to a (folder_path, agent_id) pair."""
        payload = {
            "folderPath": folder_path,
            "agentId": agent_id,
            "text": content,
            "timestamp": int(time.time() * 1000),
        }
        with self._active_context():
            return folder_observe(self.kv, payload)

    def search(
        self,
        query: str,
        folder_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search both folder-scoped observations and global memories."""
        with self._active_context():
            return folder_search(
                self.kv,
                query,
                limit=limit,
                folder_path=folder_path,
                agent_id=agent_id,
            )

    def remember(
        self, title: str, content: str, parent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Save a new insight directly to the global long-term memory."""
        payload = {
            "title": title,
            "content": content,
        }
        if parent_id:
            payload["parentId"] = parent_id

        with self._active_context():
            return remember(self.kv, payload)

    def forget(self, scope: str, key: Optional[str] = None) -> Dict[str, Any]:
        """Forget (delete) a specific memory or observation key under the given scope."""
        payload = {
            "scope": scope,
        }
        if key:
            payload["key"] = key

        with self._active_context():
            return forget(self.kv, payload)

    def get_timeline(
        self,
        folder_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve the historical feed of observations."""
        payload = {
            "limit": limit,
        }
        if folder_path:
            payload["folderPath"] = folder_path
        if agent_id:
            payload["agentId"] = agent_id
        if before is not None:
            payload["before"] = before
        if after is not None:
            payload["after"] = after

        with self._active_context():
            return folder_timeline(self.kv, payload)

    def get_graph(self) -> Dict[str, Any]:
        """Compile nodes and edges for the visual memory relation graph."""
        with self._active_context():
            return folder_graph_build(self.kv)

    def health_check(self) -> Dict[str, Any]:
        """Return system stats and diagnostic metrics."""
        with self._active_context():
            return health_check(self.kv)


class AgentCacheServer:
    def __init__(
        self,
        port: int = 3111,
        db_path: Optional[str] = None,
        secret: Optional[str] = None,
        host: str = "127.0.0.1",
    ):
        self.port = port
        self.db_path = db_path
        self.secret = secret
        self.host = host
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._start_error: Optional[Exception] = None

    def start(
        self, background: bool = False, wait: bool = True, timeout: float = 30.0
    ) -> None:
        """Boot the Flask REST + WebSocket daemon."""
        if self._thread is not None:
            raise RuntimeError("Server is already running")

        if self.db_path:
            os.environ["AGENTCACHE_DB_PATH"] = self.db_path
        if self.secret:
            os.environ["AGENTCACHE_SECRET"] = self.secret
        os.environ["III_REST_PORT"] = str(self.port)

        print(f"[server] Binding host {self.host} on port {self.port}...")

        from .app import create_app

        app = create_app()

        def run_server():
            try:
                self._server = make_server(self.host, self.port, app, threaded=True)
                self._server.serve_forever()
            except Exception as e:
                self._start_error = e
                print(f"[server] Failed to bind or run server on port {self.port}: {e}")

        if background:
            self._thread = threading.Thread(target=run_server, daemon=True)
            self._thread.start()

            if wait:
                print(
                    f"[server] Waiting for server to become ready at http://{self.host}:{self.port}/agentcache/livez ..."
                )
                import requests

                url = f"http://{self.host}:{self.port}/agentcache/livez"
                deadline = time.monotonic() + timeout
                last_error = None
                while time.monotonic() < deadline:
                    if self._thread is not None and not self._thread.is_alive():
                        raise RuntimeError(
                            f"Server thread exited before readiness check succeeded. Start error: {self._start_error}"
                        )
                    if self._start_error is not None:
                        raise RuntimeError(
                            f"Server failed to start: {self._start_error}"
                        )
                    try:
                        r = requests.get(url, timeout=0.3)
                        if r.status_code == 200:
                            print(
                                f"[server] Server is up and listening on port {self.port}."
                            )
                            return
                        last_error = RuntimeError(
                            f"Unexpected status {r.status_code} from {url}"
                        )
                    except Exception as e:
                        last_error = e
                    time.sleep(0.1)
                raise RuntimeError(f"Server failed to become ready: {last_error}")
            else:
                time.sleep(0.2)
        else:
            self._server = make_server(self.host, self.port, app, threaded=True)
            self._server.serve_forever()

    def stop(self) -> None:
        """Tear down server configurations and stop the background thread handles."""
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                print(f"[server] Warning during server shutdown: {e}")
            self._server = None
        self._thread = None
        self._start_error = None
        os.environ.pop("AGENTCACHE_DB_PATH", None)
        os.environ.pop("AGENTCACHE_SECRET", None)
        os.environ.pop("III_REST_PORT", None)
