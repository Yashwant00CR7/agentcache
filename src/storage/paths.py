"""
src/storage/paths.py — Path normalisation and ID utilities (A2.3).

Copied from src/functions.py — do NOT delete the originals (backward compat).
"""

import os
import time
import uuid
import hashlib

# Maximum allowed length for folder paths and agent IDs.
_MAX_PATH_LEN = 512


def generate_id(prefix: str) -> str:
    """Generate a time-sortable unique ID with a human-readable prefix.

    Format: ``{prefix}_{base36_timestamp}_{12_hex_chars}``
    """
    t = int(time.time() * 1000)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    ts_str = ""
    while t > 0:
        ts_str = chars[t % 36] + ts_str
        t //= 36
    if not ts_str:
        ts_str = "0"
    rand = uuid.uuid4().hex[:12]
    return f"{prefix}_{ts_str}_{rand}"


def fingerprint_id(prefix: str, content: str) -> str:
    """Generate a deterministic ID by SHA-256 fingerprinting *content*.

    Format: ``{prefix}_{first_16_hex_chars_of_sha256}``
    """
    h = hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()
    return f"{prefix}_{h[:16]}"


def normalize_folder_path(path: str) -> str:
    """Normalize a folder path for safe use in KV scope keys.

    Steps applied in order:
    1. Cap the raw input at 512 characters (REQ-066).
    2. Apply ``os.path.normpath`` to collapse redundant separators and
       resolve any ``..`` components at the OS level.
    3. Convert all OS-native separators to forward slashes.
    4. Strip any remaining leading or trailing slashes.

    Raises:
        ValueError: if *path* is empty (before or after normalization), or
                    if the normalized result still contains a ``..`` segment,
                    which would indicate an attempt at path traversal
                    (REQ-064).

    Returns:
        A non-empty, forward-slash-separated string with no leading/trailing
        slashes and no ``..`` segments — safe for use as a KV scope fragment.

    Property (REQ-074): idempotent — applying this function twice yields
    the same result as applying it once.
    """
    if not path:
        raise ValueError("folder_path must not be empty")

    # 1. Length cap before any processing.
    path = path[:_MAX_PATH_LEN]

    # Pre-normalisation traversal check: reject any path that contains a ".."
    # component in the raw input before normpath has a chance to resolve it.
    raw_parts = path.replace("\\", "/").split("/")
    if any(part == ".." for part in raw_parts):
        raise ValueError(f"folder_path contains path traversal segment '..': {path!r}")

    # 2. OS-level normalisation (resolves duplicate separators, etc.)
    normalized = os.path.normpath(path)

    # 3. Unify separators to forward slash.
    normalized = normalized.replace("\\", "/")

    # 4. Strip leading / trailing slashes.
    normalized = normalized.strip("/")

    # Guard: also reject any ".." that somehow survives normalisation.
    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"folder_path contains path traversal segment '..': {path!r}")

    if not normalized:
        raise ValueError("folder_path is empty after normalization")

    return normalized


def validate_agent_id(agent_id: str) -> str:
    """Validate and sanitize an agent_id before use in KV scope keys.

    Strips surrounding whitespace and caps at 512 characters (REQ-066).

    Raises:
        ValueError: if *agent_id* is empty after stripping.

    Returns:
        Sanitized agent_id string.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")

    sanitized = agent_id.strip()[:_MAX_PATH_LEN]

    if not sanitized:
        raise ValueError("agent_id is empty after stripping whitespace")

    return sanitized
