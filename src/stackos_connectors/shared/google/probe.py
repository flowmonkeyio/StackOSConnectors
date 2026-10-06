"""Allowlisted Google probe error facts, without consumer repair or readiness policy."""

from collections.abc import Mapping
from typing import Any

from stackos_connectors.errors import ValidationError

_APIS = {
    "google-search-console": "Google Search Console API",
    "google-analytics": "Google Analytics Admin API",
    "google-tag-manager": "Google Tag Manager API",
}
_REASONS = frozenset({"SERVICE_DISABLED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
_LEGACY_REASONS = frozenset(
    {"accessNotConfigured", "insufficientPermissions", "forbidden", "authError"}
)


def diagnostic_facts(provider: str, data: Mapping[str, Any]) -> dict[str, str]:
    """Never parse or return provider-controlled messages, URLs or response bodies."""
    api = _APIS.get(provider)
    if api is None:
        return {}
    existing = data.get("provider_reason")
    reason = (
        existing if isinstance(existing, str) and existing in _REASONS | _LEGACY_REASONS else None
    )
    payload = data.get("provider_error")
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if reason is None and isinstance(error, Mapping):
        details = error.get("details")
        if isinstance(details, list):
            for detail in details[:8]:
                if (
                    not isinstance(detail, Mapping)
                    or detail.get("@type") != "type.googleapis.com/google.rpc.ErrorInfo"
                ):
                    continue
                candidate = detail.get("reason")
                if isinstance(candidate, str) and candidate in _REASONS:
                    reason = candidate
                    break
        errors = error.get("errors")
        if reason is None and isinstance(errors, list):
            for item in errors[:8]:
                candidate = item.get("reason") if isinstance(item, Mapping) else None
                if isinstance(candidate, str) and candidate in _LEGACY_REASONS:
                    reason = candidate
                    break
    return {"provider_reason": reason, "provider_api": api} if reason is not None else {}


async def resolved_token_probe(*, auth, options, context) -> dict[str, Any]:
    """Report only supplied-token facts. No grant/resource-access claim or HTTP call."""
    if auth.method != "service-account":
        raise ValidationError("saved auth method has no credential probe")
    return {
        "ok": True,
        "status": "connected",
        "summary": (
            "Google service-account resolved token is present; resource access is unverified."
        ),
        "metadata": {"verification": "resolved_token_only", "resource_access": "unverified"},
    }
