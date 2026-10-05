"""Google Analytics 4 action connector."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import httpx

from stackos_connectors.connectors.google_analytics.integration import GoogleAnalyticsIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import IntegrationDownError, RateLimitedError, ValidationError
from stackos_connectors.shared.provider_utils import connector_error_from_integration
from stackos_connectors.shared.vendor_utils import (
    credential_payload,
    int_range,
    issue,
    optional_str,
    required_str,
    result,
    unknown_operation,
)

_MAX_LIMIT = 250_000


class GoogleAnalyticsActionConnector:
    """Decision-free adapter for GA4 reporting reads."""

    key = "google-analytics"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        match request.operation:
            case "account_summaries.list":
                int_range(payload, "page_size", issues, minimum=1, maximum=200)
                optional_str(payload, "page_cursor", issues)
            case "properties.metadata.get":
                required_str(payload, "property_id", issues)
            case "properties.run_report":
                required_str(payload, "property_id", issues)
                self._request_object(payload, issues)
                request_body = payload.get("request")
                if isinstance(request_body, dict):
                    self._required_list(request_body, "dateRanges", "$.request.dateRanges", issues)
                    self._required_list(request_body, "metrics", "$.request.metrics", issues)
                    self._optional_list(request_body, "dimensions", "$.request.dimensions", issues)
                    self._limit(request_body, "$.request.limit", issues)
                    if "property" in request_body:
                        issues.append(
                            issue(
                                "$.request.property",
                                "property belongs in property_id, not the GA4 request body",
                                "unsupported_field",
                            )
                        )
            case "properties.run_realtime_report":
                required_str(payload, "property_id", issues)
                self._request_object(payload, issues)
                request_body = payload.get("request")
                if isinstance(request_body, dict):
                    self._required_list(request_body, "metrics", "$.request.metrics", issues)
                    self._optional_list(request_body, "dimensions", "$.request.dimensions", issues)
                    self._optional_list(
                        request_body, "minuteRanges", "$.request.minuteRanges", issues
                    )
                    self._limit(request_body, "$.request.limit", issues)
                    if "property" in request_body:
                        issues.append(
                            issue(
                                "$.request.property",
                                "property belongs in property_id, not the GA4 request body",
                                "unsupported_field",
                            )
                        )
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        try:
            async with (
                nullcontext(request.options.http)
                if request.options.http is not None
                else httpx.AsyncClient(
                    timeout=(
                        request.options.timeout if request.options.timeout is not None else 60.0
                    )
                )
            ) as http:
                client = GoogleAnalyticsIntegration(
                    payload=credential_payload(request),
                    rate_limiter=request.options.rate_limiter,
                    timeout=request.options.timeout,
                    http=http,
                )
                match request.operation:
                    case "account_summaries.list":
                        call_result = await client.account_summaries_list(
                            page_size=(
                                int(payload["page_size"])
                                if payload.get("page_size") is not None
                                else None
                            ),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case "properties.metadata.get":
                        call_result = await client.metadata_get(
                            property_ref=str(payload["property_id"])
                        )
                    case "properties.run_report":
                        call_result = await client.run_report(
                            property_ref=str(payload["property_id"]),
                            request_body=dict(payload["request"]),
                        )
                    case "properties.run_realtime_report":
                        call_result = await client.run_realtime_report(
                            property_ref=str(payload["property_id"]),
                            request_body=dict(payload["request"]),
                        )
                    case _:
                        raise ValidationError(
                            f"unsupported Google Analytics operation {request.operation!r}"
                        )
        except (IntegrationDownError, RateLimitedError) as exc:
            raise connector_error_from_integration(
                exc,
                provider=self.key,
                operation=request.operation,
            ) from exc
        return self._result(request.operation, call_result.data, call_result.cost_usd)

    def _result(self, operation: str, data: Any, cost_usd: float) -> ConnectorResult:
        output = data if isinstance(data, dict) else {"data": data}
        if isinstance(output, dict):
            output = dict(output)
            next_page = output.pop("nextPageToken", None)
            if isinstance(next_page, str) and next_page:
                output["next_page_cursor"] = next_page
            property_quota = output.pop("propertyQuota", None)
            if isinstance(property_quota, dict):
                output["quota"] = self._safe_quota(property_quota)
        return result(self.key, operation, output, cost_usd)

    @classmethod
    def _safe_quota(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                cls._safe_quota_key(str(key)): cls._safe_quota(item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._safe_quota(item) for item in value]
        return value

    @staticmethod
    def _safe_quota_key(key: str) -> str:
        out: list[str] = []
        for idx, char in enumerate(key):
            if char.isupper() and idx > 0:
                out.append("_")
            out.append(char.lower())
        safe = "".join(out)
        return safe.replace("tokens", "units").replace("token", "unit")

    @staticmethod
    def _request_object(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
        value = payload.get("request")
        if not isinstance(value, dict):
            issues.append(
                issue("$.request", "request is required and must be an object", "required")
            )

    @staticmethod
    def _required_list(
        payload: dict[str, Any],
        key: str,
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            issues.append(issue(path, f"{key} must be a non-empty array", "required"))

    @staticmethod
    def _optional_list(
        payload: dict[str, Any],
        key: str,
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        value = payload.get(key)
        if value is not None and not isinstance(value, list):
            issues.append(issue(path, f"{key} must be an array", "type_mismatch"))

    @staticmethod
    def _limit(
        payload: dict[str, Any],
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        value = payload.get("limit")
        if value is None:
            return
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            issues.append(issue(path, "limit must be an integer string", "type_mismatch"))
            return
        if parsed < 1 or parsed > _MAX_LIMIT:
            issues.append(issue(path, f"limit must be between 1 and {_MAX_LIMIT}", "range"))


__all__ = ["GoogleAnalyticsActionConnector"]
