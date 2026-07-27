"""XML parsing helpers for LLM consolidation output."""

import re
from typing import List, Optional


def strip_xml_wrappers(raw: str) -> str:
    if not raw:
        return ""
    cleaned = raw.strip()
    cleaned = re.sub(r"```xml\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()
    root_match = re.search(
        r"(<[a-zA-Z_][a-zA-Z0-9_-]*>[\s\S]*<\/[a-zA-Z_][a-zA-Z0-9_-]*>)", cleaned
    )
    if root_match:
        return root_match.group(1).strip()
    return cleaned


def get_xml_tag(text: str, tag: str) -> Optional[str]:
    cleaned = strip_xml_wrappers(text)
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, cleaned, re.DOTALL)
    return match.group(1).strip() if match else None


def get_xml_children(text: str, parent_tag: str, child_tag: str) -> List[str]:
    parent_content = get_xml_tag(text, parent_tag)
    if not parent_content:
        return []
    pattern = rf"<{child_tag}>(.*?)</{child_tag}>"
    return [m.strip() for m in re.findall(pattern, parent_content, re.DOTALL)]
