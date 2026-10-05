"""DataForSEO action connector."""

from __future__ import annotations

from contextlib import nullcontext

import httpx

from stackos_connectors.connectors.dataforseo.integration import DataForSeoIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import (
    cost_cents,
    int_range,
    optional_str,
    required_str,
    result,
    str_list,
    unknown_operation,
)


class DataForSeoActionConnector:
    """Decision-free adapter for DataForSEO actions."""

    key = "dataforseo"

    _OP_COSTS = DataForSeoIntegration._PRE_EMPT_COSTS
    _MAX_GOOGLE_ADS_KEYWORDS = 1000
    _MAX_LIVE_SERP_DEPTH = 100

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        match request.operation:
            case "keyword.research" | "keyword_volume":
                str_list(payload, "keywords", issues, required=True)
                keywords = payload.get("keywords")
                if isinstance(keywords, list) and len(keywords) > self._MAX_GOOGLE_ADS_KEYWORDS:
                    issues.append(
                        ValidationIssue(
                            path="$.keywords",
                            message=(
                                "keywords must contain at most "
                                f"{self._MAX_GOOGLE_ADS_KEYWORDS} items for DataForSEO "
                                "Google Ads Live search volume"
                            ),
                            code="length",
                        )
                    )
                optional_str(payload, "location_name", issues)
                optional_str(payload, "language_code", issues)
            case "serp.analyze" | "serp":
                required_str(payload, "keyword", issues)
                optional_str(payload, "location_name", issues)
                optional_str(payload, "language_code", issues)
                int_range(payload, "depth", issues, minimum=1, maximum=self._MAX_LIVE_SERP_DEPTH)
            case "domain_intersection":
                str_list(payload, "domains", issues, required=True, length=2)
                optional_str(payload, "location_name", issues)
                optional_str(payload, "language_code", issues)
            case "keywords_for_site":
                required_str(payload, "target", issues)
                optional_str(payload, "location_name", issues)
                optional_str(payload, "language_code", issues)
            case "paa":
                required_str(payload, "keyword", issues)
                optional_str(payload, "location_name", issues)
                optional_str(payload, "language_code", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        operation = self._wrapper_operation(request.operation)
        return cost_cents(self._OP_COSTS.get(operation, 0.001))

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.auth is None:
            raise ValidationError("DataForSEO action requires a resolved credential")
        login = (request.auth.config or {}).get("login")
        if not isinstance(login, str) or not login:
            raise ValidationError("DataForSEO credential missing config_json.login")
        payload = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient(
                timeout=(request.options.timeout if request.options.timeout is not None else 60.0)
            )
        ) as http:
            client = DataForSeoIntegration(
                payload=credential_value(request, "password").encode(),
                rate_limiter=request.options.rate_limiter,
                timeout=request.options.timeout,
                http=http,
                login=login,
            )
            match request.operation:
                case "keyword.research" | "keyword_volume":
                    call_result = await client.keyword_volume(
                        keywords=list(payload["keywords"]),
                        location_name=str(payload.get("location_name", "United States")),
                        language_code=str(payload.get("language_code", "en")),
                    )
                case "serp.analyze" | "serp":
                    call_result = await client.serp(
                        keyword=str(payload["keyword"]),
                        location_name=str(payload.get("location_name", "United States")),
                        language_code=str(payload.get("language_code", "en")),
                        depth=int(payload.get("depth", 100)),
                    )
                case "domain_intersection":
                    call_result = await client.intersection(
                        domains=list(payload["domains"]),
                        location_name=str(payload.get("location_name", "United States")),
                        language_code=str(payload.get("language_code", "en")),
                    )
                case "keywords_for_site":
                    call_result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        location_name=str(payload.get("location_name", "United States")),
                        language_code=str(payload.get("language_code", "en")),
                    )
                case "paa":
                    call_result = await client.paa(
                        keyword=str(payload["keyword"]),
                        location_name=str(payload.get("location_name", "United States")),
                        language_code=str(payload.get("language_code", "en")),
                    )
                case _:
                    raise ValidationError(f"unsupported DataForSEO operation {request.operation!r}")
        return result("dataforseo", request.operation, call_result.data, call_result.cost_usd)

    def _wrapper_operation(self, operation: str) -> str:
        return {
            "keyword.research": "keyword_volume",
            "serp.analyze": "serp",
        }.get(operation, operation)


__all__ = ["DataForSeoActionConnector"]
