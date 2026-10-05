"""Jina Reader action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.connectors.jina.integration import JinaReaderIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import (
    required_str,
    result,
    unknown_operation,
)


class JinaActionConnector:
    """Decision-free adapter for Jina Reader utility actions."""

    key = "jina"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "read":
            return unknown_operation(request)
        issues: list[ValidationIssue] = []
        required_str(request.input_json, "url", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 60.0)
            )
        ) as http:
            client = JinaReaderIntegration(
                payload=(credential_value(request, "api_key").encode() if request.auth else b""),
                rate_limiter=request.options.rate_limiter,
                http=http,
            )
            call_result = await client.read(url=str(payload["url"]))
        return result("jina", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["JinaActionConnector"]
