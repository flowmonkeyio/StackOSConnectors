"""Serper.dev action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.actions.provider_utils import credential_value
from stackos_connectors.actions.vendor_utils import (
    int_range,
    optional_str,
    required_str,
    result,
    unknown_operation,
)
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.integrations.serper import SerperIntegration


class SerperActionConnector:
    """Decision-free adapter for Serper search actions."""

    key = "serper"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "search":
            return unknown_operation(request)
        issues: list[ValidationIssue] = []
        required_str(request.input_json, "query", issues)
        int_range(request.input_json, "num", issues, minimum=1, maximum=100)
        int_range(request.input_json, "page", issues, minimum=1, maximum=10)
        optional_str(request.input_json, "country", issues)
        optional_str(request.input_json, "language", issues)
        optional_str(request.input_json, "tbs", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 30.0)
            )
        ) as http:
            client = SerperIntegration(
                payload=credential_value(request, "api_key").encode(),
                rate_limiter=request.options.rate_limiter,
                http=http,
            )
            call_result = await client.search(
                query=str(payload["query"]),
                num=int(payload.get("num", 10)),
                country=str(payload["country"]) if payload.get("country") else None,
                language=str(payload["language"]) if payload.get("language") else None,
                page=int(payload["page"]) if payload.get("page") is not None else None,
                tbs=str(payload["tbs"]) if payload.get("tbs") else None,
            )
        return result("serper", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["SerperActionConnector"]
