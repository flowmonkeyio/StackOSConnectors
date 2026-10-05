"""Clay table webhook action connector.

Official docs verified:
- Clay API/webhook guidance: https://university.clay.com/docs/using-clay-as-an-api

Clay does not expose a general public REST API for all workspace operations.
This connector only submits rows to project-configured Clay table webhooks.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    credential_config,
    credential_value,
    dict_field,
    list_field,
    result,
    send_json,
    unknown_operation,
)


def _webhook_url(request: ConnectorRequest) -> str:
    config = credential_config(request)
    value = config.get("webhook_url")
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("clay credential config missing webhook URL")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError("clay webhook URL must be an HTTPS URL without credentials")
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError("clay webhook URL must not target localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if address.is_private or address.is_loopback or address.is_link_local:
        raise ValidationError("clay webhook URL must not target private networks")
    return url


class ClayActionConnector:
    """Decision-free adapter for Clay table webhook submission."""

    key = "clay"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        if request.operation != "table.webhook.submit":
            return unknown_operation(request)
        list_field(payload, "rows", issues, required=True, max_items=100)
        dict_field(payload, "metadata", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation != "table.webhook.submit":
            raise ValidationError(f"unsupported Clay operation {request.operation!r}")
        headers = {"Content-Type": "application/json"}
        try:
            token = credential_value(request, "webhook_token", "token")
        except ValidationError:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"rows": request.input_json["rows"]}
        if request.input_json.get("metadata"):
            body["metadata"] = request.input_json["metadata"]
        status, response_body, response_headers = await send_json(
            http=request.options.http,
            timeout_s=request.options.timeout if request.options.timeout is not None else 60.0,
            method="POST",
            url=_webhook_url(request),
            headers=headers,
            json_body=body,
        )
        return result(
            provider="clay",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=response_headers,
        )


__all__ = ["ClayActionConnector"]
