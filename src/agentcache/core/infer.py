"""Type inference, text extraction, and synthetic compression helpers."""

import json
import re
from typing import Any, Dict, List, Optional


def clip_embed_input(text: str) -> str:
    EMBED_MAX_CHARS = 16000
    if len(text) <= EMBED_MAX_CHARS:
        return text
    return text[:EMBED_MAX_CHARS]


def infer_type(tool_name: Optional[str], hook_type: str) -> str:
    if hook_type == "post_tool_failure":
        return "error"
    if hook_type == "prompt_submit":
        return "conversation"
    if hook_type in ("subagent_stop", "task_completed"):
        return "subagent"
    if hook_type == "notification":
        return "notification"

    if not tool_name:
        return "other"

    n = re.sub(r"([a-z])([A-Z])", r"\1_\2", tool_name)
    n = re.sub(r"[-\s]+", "_", n).lower()

    def has_word(word: str) -> bool:
        return (
            bool(re.search(rf"(^|_){word}(_|$)", n))
            or n == word
            or n.endswith(word)
            or n.startswith(word)
        )

    if any(has_word(w) for w in ["fetch", "http", "web"]):
        return "web_fetch"
    if any(has_word(w) for w in ["grep", "search", "glob", "find"]):
        return "search"
    if any(has_word(w) for w in ["bash", "shell", "exec", "run"]):
        return "command_run"
    if any(has_word(w) for w in ["edit", "update", "patch", "replace"]):
        return "file_edit"
    if any(has_word(w) for w in ["write", "create"]):
        return "file_write"
    if any(has_word(w) for w in ["read", "view"]):
        return "file_read"
    if any(has_word(w) for w in ["task", "agent"]):
        return "subagent"
    return "other"


def extract_files(input_data: Any) -> List[str]:
    if not input_data or not isinstance(input_data, dict):
        return []
    out = set()
    for key in ["file_path", "filepath", "path", "filePath", "file", "pattern"]:
        v = input_data.get(key)
        if isinstance(v, str) and 0 < len(v) < 512:
            out.add(v)
    return list(out)


def stringify_for_narrative(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v)
    except Exception:
        return str(v)


def vector_index_add_guarded(
    obs_id: str, session_id: str, text: str, context: Dict[str, Any]
) -> bool:
    from .. import legacy as _legacy

    if _legacy._search_service is None:
        return False
    ep = _legacy._search_service.embedding_provider
    vi = _legacy._search_service.vector
    if not vi or not ep:
        return False
    try:
        clipped = clip_embed_input(text)
        embedding = ep.embed(clipped)
        if len(embedding) != ep.dimensions:
            print(
                f"[vector-index] Dimension mismatch: expected {ep.dimensions}, got {len(embedding)}"
            )
            return False
        vi.add(obs_id, session_id, embedding)
        return True
    except Exception as e:
        print(f"[vector-index] Embed failed: {e}")
        return False


def build_synthetic_compression(raw: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = raw.get("toolName") or raw.get("hookType")
    input_str = stringify_for_narrative(raw.get("toolInput"))
    output_str = stringify_for_narrative(raw.get("toolOutput"))
    prompt_str = raw.get("userPrompt") or ""

    parts = [s for s in [prompt_str, input_str, output_str] if len(s) > 0]
    narrative = " | ".join(parts)
    if len(narrative) > 400:
        narrative = narrative[:399] + "…"

    title = tool_name or "observation"
    if len(title) > 80:
        title = title[:79] + "…"

    subtitle = None
    if input_str:
        subtitle = input_str
        if len(subtitle) > 120:
            subtitle = subtitle[:119] + "…"

    res = {
        "id": raw["id"],
        "sessionId": raw["sessionId"],
        "timestamp": raw["timestamp"],
        "type": infer_type(raw.get("toolName"), raw["hookType"]),
        "title": title,
        "subtitle": subtitle,
        "facts": [],
        "narrative": narrative,
        "concepts": [],
        "files": extract_files(raw.get("toolInput")),
        "importance": 5,
        "confidence": 0.3,
    }
    for k in ["modality", "imageData", "agentId"]:
        if raw.get(k) is not None:
            res[k] = raw[k]
    return res
