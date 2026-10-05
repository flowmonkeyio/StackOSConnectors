"""Firecrawl action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.connectors.firecrawl.integration import FirecrawlIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import (
    bool_field,
    cost_cents,
    int_range,
    optional_str,
    required_str,
    result,
    str_list,
    unknown_operation,
)


class FirecrawlActionConnector:
    """Decision-free adapter for Firecrawl utility actions."""

    key = "firecrawl"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        match request.operation:
            case "scrape":
                required_str(payload, "url", issues)
                str_list(payload, "formats", issues)
                bool_field(payload, "only_main_content", issues)
            case "crawl":
                required_str(payload, "url", issues)
                int_range(payload, "max_depth", issues, minimum=1, maximum=10)
                int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case "map":
                required_str(payload, "url", issues)
                optional_str(payload, "search", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        operation = request.operation
        if operation == "crawl":
            limit = request.input_json.get("limit", 25)
            pages = limit if isinstance(limit, int) and not isinstance(limit, bool) else 25
            return cost_cents(FirecrawlIntegration._ESTIMATED_COSTS["crawl"] * pages)
        cost = FirecrawlIntegration._ESTIMATED_COSTS.get(operation, 0.001)
        return cost_cents(cost)

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 60.0)
            )
        ) as http:
            client = FirecrawlIntegration(
                payload=credential_value(request, "api_key").encode(),
                rate_limiter=request.options.rate_limiter,
                timeout=request.options.timeout,
                http=http,
            )
            match request.operation:
                case "scrape":
                    call_result = await client.scrape(
                        url=str(payload["url"]),
                        formats=payload.get("formats"),
                        only_main_content=bool(payload.get("only_main_content", True)),
                    )
                case "crawl":
                    call_result = await client.crawl(
                        url=str(payload["url"]),
                        max_depth=int(payload.get("max_depth", 2)),
                        limit=int(payload.get("limit", 25)),
                    )
                case "map":
                    call_result = await client.map(
                        url=str(payload["url"]),
                        search=payload.get("search"),
                    )
                case _:
                    raise ValidationError(f"unsupported Firecrawl operation {request.operation!r}")
        return result("firecrawl", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["FirecrawlActionConnector"]
