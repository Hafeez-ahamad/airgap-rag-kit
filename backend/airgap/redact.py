"""Redaction for anything exported or shown in diagnostics (bug reports, logs, UI).

Removes: user names inside home-directory paths, e-mail addresses, bearer/API tokens,
``key=value`` secrets, and URLs' userinfo. Conservative by design: it may over-redact.
"""

from __future__ import annotations

import re
from typing import Any

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)([A-Z]:\\(?:Users|Documents and Settings)\\)[^\\/\s\"']+"), r"\1<user>"),
    (re.compile(r"(/(?:home|Users)/)[^/\s\"']+"), r"\1<user>"),
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "<email>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}\b"), "<token>"),
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{10,}\b"), "<token>"),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|api[_-]?key)[A-Z0-9_]*)"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2<redacted>",
    ),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact strings inside JSON-like data. Secret-named keys lose their value."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            secretish = re.search(r"(?i)password|secret|token|api[_-]?key", str(k))
            out[k] = "<redacted>" if secretish and v not in (None, "") else redact(v)
        return out
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    out = redact(value)
    assert isinstance(out, dict)
    return out
