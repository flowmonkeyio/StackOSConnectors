"""Salesloft API action connector.

Official docs verified:
- Cadence membership create: https://developers.salesloft.com/docs/api/cadence-memberships-create/
- Authentication: https://developer.salesloft.com/docs/platform/api-basics/api-key-authentication/
- Rate limits: https://developer.salesloft.com/docs/platform/api-basics/rate-limits/
"""

from __future__ import annotations

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    config_str,
    credential_value,
    required_str,
    result,
    send_json,
    unknown_operation,
)


def _base_url(request: ConnectorRequest) -> str:
    return (config_str(request, "base_url", default="https://api.salesloft.com") or "").rstrip("/")


def _headers(request: ConnectorRequest) -> dict[str, str]:
    auth_method_key = request.auth.method if request.auth is not None else None
    if auth_method_key == "api_key":
        token = credential_value(request, "api_key")
    elif auth_method_key in {"oauth2_authorization_code", "oauth2_token"}:
        token = credential_value(request, "access_token")
    else:
        raise ValidationError(
            "Salesloft action requires a recognized saved auth method",
            data={"auth_method_key": auth_method_key},
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


class SalesloftActionConnector:
    """Decision-free adapter for Salesloft cadence membership creation."""

    key = "salesloft"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        # Validate native numeric IDs without changing their execution values.
        payload = dict(request.input_json)
        for key in ("cadence_id", "person_id"):
            if isinstance(payload.get(key), int) and not isinstance(payload[key], bool):
                payload[key] = str(payload[key])
        issues: list[ValidationIssue] = []
        if request.operation != "cadence_membership.create":
            return unknown_operation(request)
        required_str(payload, "cadence_id", issues)
        required_str(payload, "person_id", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation != "cadence_membership.create":
            raise ValidationError(f"unsupported Salesloft operation {request.operation!r}")
        payload = request.input_json
        body = {
            "cadence_id": payload["cadence_id"],
            "person_id": payload["person_id"],
        }
        if payload.get("user_id"):
            body["user_id"] = payload["user_id"]
        status, response_body, headers = await send_json(
            http=request.options.http,
            timeout_s=request.options.timeout if request.options.timeout is not None else 60.0,
            method="POST",
            url=f"{_base_url(request)}/v2/cadence_memberships",
            headers=_headers(request),
            json_body=body,
        )
        return result(
            provider="salesloft",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=headers,
        )


__all__ = ["SalesloftActionConnector"]
