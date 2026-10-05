"""WordPress action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.connectors.wordpress.integration import WordPressIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.vendor_utils import (
    credential_config_str,
    credential_payload,
    required_dict,
    result,
    unknown_operation,
)


class WordPressActionConnector:
    """Decision-free adapter for WordPress publishing actions."""

    key = "wordpress"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "post.create":
            return unknown_operation(request)
        issues: list[ValidationIssue] = []
        required_dict(request.input_json, "post", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation != "post.create":
            raise ValidationError(f"unsupported WordPress operation {request.operation!r}")
        payload = request.input_json
        site_url = credential_config_str(
            request,
            "wp_url",
            "site_url",
            "base_url",
            label="config_json.wp_url",
        )
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 60.0)
            )
        ) as http:
            client = WordPressIntegration(
                payload=credential_payload(request),
                rate_limiter=request.options.rate_limiter,
                timeout=request.options.timeout,
                http=http,
                site_url=site_url,
            )
            call_result = await client.create_post(post=dict(payload["post"]))
        return result("wordpress", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["WordPressActionConnector"]
