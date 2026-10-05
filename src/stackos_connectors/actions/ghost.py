"""Ghost action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.actions.vendor_utils import (
    credential_config_str,
    credential_payload,
    optional_str,
    required_dict,
    result,
    unknown_operation,
)
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
    thaw,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.integrations.ghost import GhostIntegration


class GhostActionConnector:
    """Decision-free adapter for Ghost publishing actions."""

    key = "ghost"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "post.create":
            return unknown_operation(request)
        issues: list[ValidationIssue] = []
        required_dict(request.input_json, "post", issues)
        optional_str(request.input_json, "source", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation != "post.create":
            raise ValidationError(f"unsupported Ghost operation {request.operation!r}")
        payload = request.input_json
        site_url = credential_config_str(
            request,
            "ghost_url",
            "site_url",
            "base_url",
            label="config_json.ghost_url",
        )
        config = thaw(request.auth.config) if request.auth is not None else {}
        api_version = "v5.0"
        if isinstance(config, dict) and isinstance(config.get("api_version"), str):
            api_version = str(config["api_version"])
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 60.0)
            )
        ) as http:
            client = GhostIntegration(
                payload=credential_payload(request),
                rate_limiter=request.options.rate_limiter,
                http=http,
                site_url=site_url,
                api_version=api_version,
            )
            call_result = await client.create_post(
                post=dict(payload["post"]),
                source=str(payload.get("source", "html")),
            )
        return result("ghost", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["GhostActionConnector"]
