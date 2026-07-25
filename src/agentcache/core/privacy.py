"""Privacy and data-scrubbing helpers. Zero dependencies on other core modules."""

import re

PRIVATE_TAG_RE = re.compile(r"<private>[\s\S]*?</private>", re.IGNORECASE)

SECRET_PATTERN_SOURCES = [
    re.compile(
        r'(?:api[_-]?key|secret|token|password|credential|auth)[\s]*[=:]\s*["\']?[A-Za-z0-9_\-/.+]{20,}["\']?',
        re.IGNORECASE,
    ),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}", re.IGNORECASE),
    re.compile(r"sk-proj-[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"(?:sk|pk|rk|ak)-[A-Za-z0-9][A-Za-z0-9\-_]{19,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"gh[pus]_[A-Za-z0-9]{36,}", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}", re.IGNORECASE),
    re.compile(r"xoxb-[A-Za-z0-9\-]+", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
    re.compile(r"AIza[A-Za-z0-9\-_]{35}", re.IGNORECASE),
    re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", re.IGNORECASE
    ),
    re.compile(r"npm_[A-Za-z0-9]{36}", re.IGNORECASE),
    re.compile(r"glpat-[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"dop_v1_[A-Za-z0-9]{64}", re.IGNORECASE),
]


def strip_private_data(input_str: str) -> str:
    result = PRIVATE_TAG_RE.sub("[REDACTED]", input_str)
    for pattern in SECRET_PATTERN_SOURCES:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result
