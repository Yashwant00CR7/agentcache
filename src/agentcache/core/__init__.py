"""
agentcache.core — deep, injectable modules for the agentcache system.

Modules:
  kv_scopes      — KV scope key registry (shared by all stores and routes)
  search_service — SearchService (BM25 + vector indexing and querying)
"""

from .kv_scopes import KV
from .observation_store import ObservationEvents, ObservationStore
from .search_service import IndexPersistence, SearchService

__all__ = ["KV", "SearchService", "IndexPersistence", "ObservationStore", "ObservationEvents"]

