"""
agentcache — A Python REST + WebSocket + MCP cache server for AI agents, backed by SQLite.
"""

__version__ = "0.9.8"

from .app import create_app
from .connect import run_connect
from .core import KV, ObservationEvents, ObservationStore, SearchService
from .db import StateKV
from .legacy import (
    folder_graph_build,
    health_check,
    remember,
)

__all__ = [
    "__version__",
    "create_app",
    "StateKV",
    "run_connect",
    "KV",
    "ObservationStore",
    "ObservationEvents",
    "SearchService",
    "folder_graph_build",
    "remember",
    "health_check",
]
