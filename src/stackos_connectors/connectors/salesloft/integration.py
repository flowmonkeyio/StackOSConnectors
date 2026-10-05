"""Salesloft credential probes.

Official docs: https://developers.salesloft.com/docs/api/me-index/
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.redaction import redact_secret_text
from stackos_connectors.shared.base import BaseIntegration

_ME_URL = "https://api.salesloft.com/v2/me"


class SalesloftCredentialConfigurationError(ValueError):
    """Raised when a resolved Salesloft credential is unusable."""


def _payload_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8").strip()
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SalesloftCredentialConfigurationError(
            "Salesloft credential payload must be a JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise SalesloftCredentialConfigurationError(
            "Salesloft credential payload must be a JSON object"
        )
    return value


def _required_token(payload: Mapping[str, Any], key: str) -> str:
    token = payload.get(key)
    if not isinstance(token, str) or not token.strip():
        raise SalesloftCredentialConfigurationError(f"Salesloft credential missing {key}")
    return token.strip()


class SalesloftIntegration(BaseIntegration):
    """Read-only current-user probe selected from saved Salesloft auth method."""

    kind = "salesloft"
    vendor = "salesloft"
    default_qps = 1.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            self._payload = _payload_object(self.payload)
        except SalesloftCredentialConfigurationError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": self.vendor},
            ) from exc

    async def test_credentials(self) -> dict[str, Any]:
        method = self.probe_context.auth_method_key if self.probe_context else ""
        if method == "api_key":
            token = _required_token(self._payload, "api_key")
        elif method in {"oauth2_authorization_code", "oauth2_token"}:
            token = _required_token(self._payload, "access_token")
        else:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "unsupported_auth_method",
                "summary": "Salesloft credential test requires a recognized saved auth method.",
            }
        result = await self.call(
            op="auth.test",
            method="GET",
            url=_ME_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        body = result.data if isinstance(result.data, Mapping) else {}
        if not body:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "invalid_response",
                "summary": "Salesloft auth probe did not return the current user.",
            }
        output: dict[str, Any] = {"ok": True, "vendor": self.vendor, "status": "ok"}
        for body_key, output_key in (
            ("id", "user_id"),
            ("guid", "user_guid"),
            ("name", "user_name"),
        ):
            value = body.get(body_key)
            if value is not None:
                output[output_key] = str(value)
        account_id = body.get("guid") or body.get("id")
        if account_id is not None:
            output["metadata"] = {
                "evidence": {
                    "account": {
                        "provider_account_id": str(account_id),
                        "display_name": (
                            str(body.get("name")) if body.get("name") is not None else None
                        ),
                        "metadata": {
                            "user_id": body.get("id"),
                            "user_guid": body.get("guid"),
                            "user_name": body.get("name"),
                        },
                    }
                }
            }
        # This endpoint is a credential-health probe, never grant evidence.
        return output


__all__ = ["SalesloftIntegration"]
