"""
src/storage/ — Storage utilities package for agentmemory-python (A2.3).

Sub-modules:
  scopes  — KV scope registry (class KV with all scope definitions)
  paths   — Path/ID utility functions (normalize_folder_path, validate_agent_id,
             generate_id, fingerprint_id)
  images  — Image-on-disk helpers (save_image_to_disk, delete_image, touch_image,
             is_managed_image_path)

These are compatibility copies — the originals in functions.py are kept intact
for backward compatibility.
"""

from .scopes import KV
from .paths import normalize_folder_path, validate_agent_id, generate_id, fingerprint_id
from .images import save_image_to_disk, delete_image, touch_image, is_managed_image_path

__all__ = [
    "KV",
    "normalize_folder_path", "validate_agent_id", "generate_id", "fingerprint_id",
    "save_image_to_disk", "delete_image", "touch_image", "is_managed_image_path",
]
