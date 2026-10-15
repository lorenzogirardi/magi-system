"""Redaction of credentials from anything persisted or logged (brief §0.5, §13)."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^/\s:@]+:[^/\s@]+@"),  # scheme://user:pass@
    re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^/\s:@]{20,}@"),  # scheme://token@
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(authorization:\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b((?:api[_-]?key|token|secret|password|passwd)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{6,}"
    ),
)

SECRET_ENV_HINTS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL", "PRIVATE")


def secret_env_values(env: dict[str, str] | None = None) -> tuple[str, ...]:
    env = dict(os.environ) if env is None else env
    return tuple(
        v for k, v in env.items() if v and len(v) >= 8 and any(h in k.upper() for h in SECRET_ENV_HINTS)
    )


class Redactor:
    def __init__(self, extra_secrets: Iterable[str] = (), *, include_env: bool = True) -> None:
        secrets = set(s for s in extra_secrets if s and len(s) >= 4)
        if include_env:
            secrets.update(secret_env_values())
        self._literals = tuple(sorted(secrets, key=len, reverse=True))

    def text(self, value: str) -> str:
        for lit in self._literals:
            value = value.replace(lit, REDACTED)
        for pat in _PATTERNS:
            if pat.groups:
                value = pat.sub(lambda m: m.group(1) + REDACTED, value)
            else:
                value = pat.sub(
                    lambda m: (m.group(0).split("://")[0] + "://" + REDACTED + "@") if "://" in m.group(0) else REDACTED,
                    value,
                )
        return value

    def value(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self.value(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self.value(v) for v in obj)
        return obj
