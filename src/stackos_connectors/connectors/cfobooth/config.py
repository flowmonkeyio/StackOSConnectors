"""Explicit CFO destination and account binding; no fallback or credential custody."""

import re
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

import httpx

from stackos_connectors.connectors.trackbooth.integration import normalize_trackbooth_base_url
from stackos_connectors.errors import ValidationError

from .contract import validator


def normalize_api_base_url(raw: str | None) -> str:
    """Reuse native Trackbooth HTTPS/loopback policy, requiring an explicit origin."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("CFOBooth api_base_url must be explicitly configured")
    raw = raw.strip()
    if re.search(r"[\s\\%?#]", raw):
        raise ValidationError("CFOBooth API URL contains unsupported components")
    try:
        parsed = urlsplit(raw)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        path = parsed.path.rstrip("/")
        if any(part in {".", ".."} for part in path.split("/")) or "//" in path:
            raise ValueError
        if parsed.netloc.endswith(":") or "@" in parsed.netloc:
            raise ValueError
        url = httpx.URL(parsed.geturl())
        if not url.host or not url.is_absolute_url:
            raise ValueError
        # Apply native policy to the final wire destination, after HTTPX's IDNA
        # normalization. Use its validation, not its path rewrite: urlparse-based
        # rewriting drops a configured final segment's semicolon parameters.
        normalize_trackbooth_base_url(str(url))
        host = url.raw_host.decode("ascii").lower().rstrip(".")
        if host in {"cfobooth.com", "www.cfobooth.com"}:
            raise ValidationError("cfobooth.com is a display-only example; configure your endpoint")
        if url.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise ValidationError("CFOBooth API URL must use HTTPS or exact loopback HTTP") from None
    return str(url).rstrip("/")


def organization_id(config: Mapping) -> str:
    value = config.get("organization_id")
    pattern = validator("current-key-v1").schema["properties"]["data"]["properties"][
        "organizationId"
    ]["pattern"]
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValidationError("CFOBooth organization_id must be a UUID")
    return value


def validate_auth(auth, options) -> tuple[str, str, str]:
    if auth is None or auth.method != "api_key":
        raise ValidationError("CFOBooth requires native api_key authentication")
    base = normalize_api_base_url(auth.config.get("api_base_url"))
    org = organization_id(auth.config)
    if options.provider_context:
        raise ValidationError("CFOBooth does not accept caller URL, header or scope overrides")
    key = auth.fields.get("api_key")
    if not isinstance(key, str) or not key or re.search(r"[^\x21-\x7e]", key):
        raise ValidationError("CFOBooth api_key must be an opaque nonempty header value")
    return base, org, key


def path_value(value: str) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x1f\x7f\\]", value):
        raise ValidationError("CFOBooth path parameters must be nonempty safe strings")
    decoded = value
    for _ in range(3):
        if any(part in {".", ".."} for part in decoded.split("/")):
            raise ValidationError("CFOBooth path traversal is forbidden")
        decoded = unquote(decoded)
    return value
