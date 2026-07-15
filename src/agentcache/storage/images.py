"""
src/storage/images.py — Image-on-disk storage helpers (A2.3).

Copied from src/functions.py — do NOT delete the originals (backward compat).
"""

import hashlib
import os
from typing import Optional, Tuple

IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".agentmemory", "images")


def get_max_bytes() -> int:
    """Return the configured image-store byte limit (default 500 MB)."""
    return int(os.getenv("AGENTMEMORY_IMAGE_STORE_MAX_BYTES", 500 * 1024 * 1024))


def is_managed_image_path(file_path: str) -> bool:
    """Return True iff *file_path* lives inside the managed images directory."""
    if not file_path:
        return False
    resolved = os.path.abspath(file_path)
    normalized_images_dir = os.path.abspath(IMAGES_DIR)
    return (
        resolved.startswith(normalized_images_dir + os.sep)
        or resolved == normalized_images_dir
    )


def save_image_to_disk(base64_data: str) -> Tuple[str, int]:
    """Decode *base64_data* and write to the managed images directory.

    Returns:
        (file_path, bytes_written) — bytes_written is 0 if the file already
        existed (content-addressable dedup via SHA-256).
    """
    if not base64_data:
        return "", 0

    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR, exist_ok=True)

    clean_base64 = base64_data
    ext = "png"

    if base64_data.startswith("data:image/"):
        comma_idx = base64_data.find(",")
        if comma_idx != -1:
            meta = base64_data[:comma_idx]
            if "jpeg" in meta or "jpg" in meta:
                ext = "jpg"
            elif "webp" in meta:
                ext = "webp"
            elif "gif" in meta:
                ext = "gif"
            clean_base64 = base64_data[comma_idx + 1 :]
    elif base64_data.startswith("/9j/"):
        ext = "jpg"

    h = hashlib.sha256(clean_base64.encode("utf-8")).hexdigest()
    file_path = os.path.join(IMAGES_DIR, f"{h}.{ext}")

    if os.path.exists(file_path):
        return file_path, 0

    import base64

    buffer = base64.b64decode(clean_base64)
    with open(file_path, "wb") as f:
        f.write(buffer)

    size = os.path.getsize(file_path)
    return file_path, size


def delete_image(file_path: Optional[str]) -> int:
    """Delete a managed image file and return the number of bytes freed.

    Returns 0 if the path is not managed or does not exist.
    """
    if not file_path or not is_managed_image_path(file_path):
        return 0
    try:
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            os.remove(file_path)
            return size
    except Exception as e:
        print(f"[agentmemory] Failed to delete image context: {e}")
    return 0


def touch_image(file_path: str) -> None:
    """Update the mtime of a managed image (keeps it alive past LRU eviction)."""
    if not file_path or not is_managed_image_path(file_path):
        return
    try:
        if os.path.exists(file_path):
            os.utime(file_path, None)
    except Exception:
        pass
