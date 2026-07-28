"""Agent configuration helpers — env-var readers and Dolt commit helper."""

import os
from typing import Optional

from ..db import StateKV


def get_agent_id() -> Optional[str]:
    return os.getenv("AGENT_ID") or None


def commit_if_enabled(
    kv: StateKV, message: str, agent_id: Optional[str]
) -> Optional[str]:
    return kv.commit_version(message, agent_id or "unknown-agent")


def is_agent_scope_isolated() -> bool:
    return (
        os.getenv("AGENTCACHE_AGENT_SCOPE") or os.getenv("AGENTMEMORY_AGENT_SCOPE")
    ) == "isolated"


def is_graph_extraction_enabled() -> bool:
    return os.getenv("GRAPH_EXTRACTION_ENABLED") == "true"


def is_consolidation_enabled() -> bool:
    val = os.getenv("CONSOLIDATION_ENABLED")
    if val in ("false", "0"):
        return False
    if val in ("true", "1"):
        return True
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
