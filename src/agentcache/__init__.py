"""
agentcache — A Python REST + WebSocket + MCP cache server for AI agents, backed by SQLite.
"""

__version__ = "0.9.8"

from .app import create_app
from .connect import run_connect
from .db import StateKV
from .functions import (
    folder_graph_build,
    folder_observe,
    folder_search,
    folder_timeline,
    forget,
    health_check,
    remember,
)

__all__ = [
    "__version__",
    "create_app",
    "StateKV",
    "run_connect",
    "folder_observe",
    "folder_search",
    "folder_timeline",
    "folder_graph_build",
    "remember",
    "forget",
    "health_check",
]
