"""Remove secrets from text before it is shown or stored."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]\s*([^\s,;\"']+)"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    if not text:
        return ""
    cleaned = text
    for secret in extra_secrets or []:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _PATTERNS[0].sub(lambda match: f"{match.group(1)}=[REDACTED]", cleaned)
    for pattern in _PATTERNS[1:]:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def redact_value(value: Any, extra_secrets: list[str] | None = None) -> Any:
    if isinstance(value, str):
        return redact_text(value, extra_secrets)
    if isinstance(value, list):
        return [redact_value(item, extra_secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(item, extra_secrets) for key, item in value.items()}
    return value
