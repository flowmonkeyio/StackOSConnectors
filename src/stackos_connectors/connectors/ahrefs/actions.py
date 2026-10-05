"""Named native Ahrefs API requests; admission decisions belong to the caller."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.connectors.ahrefs.integration import AhrefsIntegration
from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import (
    issue,
    optional_str,
    required_str,
    unknown_operation,
)

AHREFS_DEFAULT_ROWS = 100
AHREFS_MODE_VALUES = ("exact", "prefix", "domain", "subdomains")


def _positive_limit(payload, issues):
    value = payload.get("limit")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
        issues.append(issue("$.limit", "limit must be a positive integer", "range"))


class AhrefsActionConnector:
    key = "ahrefs"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues = []
        match request.operation:
            case "limits_and_usage":
                pass
            case "competitor.keywords" | "keywords_for_site":
                required_str(payload, "target", issues)
                optional_str(payload, "country", issues)
                optional_str(payload, "date", issues)
                _positive_limit(payload, issues)
            case "backlink.research" | "top_backlinks":
                required_str(payload, "target", issues)
                optional_str(payload, "mode", issues)
                if (
                    isinstance(payload.get("mode"), str)
                    and payload["mode"] not in AHREFS_MODE_VALUES
                ):
                    issues.append(
                        issue(
                            "$.mode",
                            "mode must be one of exact, prefix, domain, subdomains",
                            "enum_mismatch",
                        )
                    )
                _positive_limit(payload, issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=request.options.timeout if request.options.timeout is not None else 60.0
            )
        ) as http:
            client = AhrefsIntegration(
                payload=credential_value(request, "api_key").encode(),
                http=http,
                rate_limiter=request.options.rate_limiter,
                timeout=request.options.timeout,
            )
            match request.operation:
                case "limits_and_usage":
                    result = await client.limits_and_usage()
                case "competitor.keywords" | "keywords_for_site":
                    result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        country=str(payload.get("country", "us")),
                        limit=int(payload.get("limit", AHREFS_DEFAULT_ROWS)),
                        date_=payload.get("date"),
                    )
                case "backlink.research" | "top_backlinks":
                    result = await client.top_backlinks(
                        target=str(payload["target"]),
                        mode=str(payload.get("mode", "domain")),
                        limit=int(payload.get("limit", AHREFS_DEFAULT_ROWS)),
                    )
                case _:
                    raise ValidationError(f"unsupported Ahrefs operation {request.operation!r}")
        return ConnectorResult(
            output_json=result.data if isinstance(result.data, dict) else {"data": result.data},
            metadata_json={
                "vendor": "ahrefs",
                "operation": request.operation,
                **(result.metadata or {}),
            },
            cost_cents=0,
        )
