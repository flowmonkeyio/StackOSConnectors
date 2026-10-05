"""Redaction helpers for agent-visible artifact metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEY_PARTS = (
    "service_account_json",
    "assertion",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)([\"']?(?:access[_-]?token|api[_-]?key|apikey|authorization|client[_-]?secret|"
    r"service[_-]?account[_-]?json|assertion|credential|password|private[_-]?key|refresh[_-]?token|secret|token)[\"']?\s*[:=]\s*"
    r"[\"']?)(?!bearer\b)([^\"'\s,;}&]+)"
)
_AUTH_BEARER_TEXT_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}&]+")
_BEARER_TEXT_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_TELEGRAM_BOT_URL_RE = re.compile(r"(?i)(/bot)\d{5,}:[A-Za-z0-9_-]+(?=/)")
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)
_SIGNED_URL_PARAM_RE = re.compile(
    r"(?i)([?&](?:x-amz-[^=&\s\"']+|x-goog-[^=&\s\"']+|x-oss-[^=&\s\"']+|"
    r"signature|sig|expires|expiresat|policy|security-token|token)=)[^&#\s\"']+"
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def redact_secrets(value: Any) -> Any:
    """Return a deep copy with secret-like object keys redacted."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            redacted[key] = "[redacted]" if _is_sensitive_key(key) else redact_secrets(raw_value)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def redact_secret_text(value: str) -> str:
    """Redact secret-like assignments inside vendor-controlled text."""
    redacted = _PEM_PRIVATE_KEY_RE.sub("[redacted private key]", value)
    redacted = _AUTH_BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _SIGNED_URL_PARAM_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _SECRET_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _TELEGRAM_BOT_URL_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    return _BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)


__all__ = ["redact_secret_text", "redact_secrets"]


def redact_secret_values(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    """Redact exact credential values from controlled JSON and text outputs."""
    values = tuple(item for item in sensitive_values if item)
    if not values:
        return value

    def redact_text(text: str) -> str:
        redacted = text
        for secret in values:
            redacted = redacted.replace(secret, "[redacted]")
        return redacted

    if isinstance(value, Mapping):
        return {
            redact_text(str(key)): redact_secret_values(item, values) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_values(item, values) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secret_values(item, values) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def auth_secret_values(auth: Any) -> tuple[str, ...]:
    if auth is None:
        return ()

    def values(value: Any) -> list[str]:
        if isinstance(value, Mapping):
            return [secret for item in value.values() for secret in values(item)]
        if isinstance(value, list | tuple):
            return [secret for item in value for secret in values(item)]
        return [value] if isinstance(value, str) and value else []

    fields = auth.get("fields", {}) if isinstance(auth, Mapping) else auth.fields
    config = auth.get("config", {}) if isinstance(auth, Mapping) else auth.config

    def config_secrets(value: Any) -> list[str]:
        if isinstance(value, Mapping):
            return [
                secret
                for key, item in value.items()
                for secret in (
                    values(item) if _is_sensitive_key(str(key)) else config_secrets(item)
                )
            ]
        if isinstance(value, list | tuple):
            return [secret for item in value for secret in config_secrets(item)]
        return []

    secrets = values(fields) + config_secrets(config)
    return tuple(sorted(set(secrets), key=len, reverse=True))
