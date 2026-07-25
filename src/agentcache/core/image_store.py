"""Image storage helpers — disk persistence and extraction from payloads."""

import hashlib
import os
from typing import Any, Optional, Tuple

IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".agentcache", "images")


def get_max_bytes() -> int:
    return int(
        os.getenv("AGENTCACHE_IMAGE_STORE_MAX_BYTES")
        or os.getenv("AGENTMEMORY_IMAGE_STORE_MAX_BYTES")
        or 500 * 1024 * 1024
    )


def is_managed_image_path(file_path: str) -> bool:
    if not file_path:
        return False
    resolved = os.path.abspath(file_path)
    normalized_images_dir = os.path.abspath(IMAGES_DIR)
    return (
        resolved.startswith(normalized_images_dir + os.sep)
        or resolved == normalized_images_dir
    )


def save_image_to_disk(base64_data: str) -> Tuple[str, int]:
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
    if not file_path or not is_managed_image_path(file_path):
        return 0
    try:
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            os.remove(file_path)
            return size
    except Exception as e:
        print(f"[agentcache] Failed to delete image context: {e}")
    return 0


def touch_image(file_path: str) -> None:
    if not file_path or not is_managed_image_path(file_path):
        return
    try:
        if os.path.exists(file_path):
            os.utime(file_path, None)
    except Exception:
        pass


def extract_image(d: Any) -> Optional[str]:
    if not d:
        return None
    if isinstance(d, str):
        if (
            d.startswith("data:image/")
            or d.startswith("iVBORw0KGgo")
            or d.startswith("/9j/")
        ):
            return d
        return None
    if isinstance(d, dict):
        for k in ["image_data", "image_path", "imageBase64", "imagePath"]:
            if isinstance(d.get(k), str):
                return d[k]
        for key, val in d.items():
            match = extract_image(val)
            if match:
                return match
    return None
