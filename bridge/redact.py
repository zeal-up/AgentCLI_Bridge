"""Best-effort redaction before local transcripts are mirrored to Miaoda."""
from __future__ import annotations

import re
from typing import Any


_SECRET_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"), "sk-[redacted]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "aws-key-[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_[redacted]"),
    (re.compile(r"\bgh[opsru]_[A-Za-z0-9_]{20,}\b"), "gh_[redacted]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "glpat-[redacted]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "xox-[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "AIza[redacted]"),
)

_PERSONAL_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[redacted-email]",
    ),
)

_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd)"
    r"(\s*[:=：]\s*)([\"']?)([^\"'\s`,;]{8,})([\"']?)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]+=*")


def redact_text(value: Any, *, include_personal: bool = True) -> Any:
    """Return *value* with common credentials and direct identifiers redacted."""
    if not isinstance(value, str) or not value:
        return value

    text = value.replace("\x00", "")
    text = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]{m.group(5)}", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    for pattern, replacement in _SECRET_REDACTIONS:
        text = pattern.sub(replacement, text)
    if include_personal:
        for pattern, replacement in _PERSONAL_REDACTIONS:
            text = pattern.sub(replacement, text)
    return text


def is_redacted(value: Any) -> bool:
    return redact_text(value) != value
