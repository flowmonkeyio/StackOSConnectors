"""Pipedrive API action connector.

Official docs verified:
- Deals API v2: https://developers.pipedrive.com/docs/api/v1/Deals
- API authentication concepts: https://pipedrive.readme.io/docs/core-api-concepts-authentication
- API v2 overview: https://pipedrive.readme.io/docs/pipedrive-api-v2
"""

from __future__ import annotations

from typing import Any

from stackos_connectors.connectors.pipedrive.integration import (
    PipedriveCredentialConfigurationError,
    normalize_pipedrive_api_domain,
)
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    config_str,
    credential_value,
    int_range,
    optional_str,
    result,
    send_json,
    unknown_operation,
)


def _base_url(request: ConnectorRequest) -> str:
    base = config_str(request, "api_domain") or config_str(request, "base_url")
    try:
        if base:
            return normalize_pipedrive_api_domain(base)
        domain = config_str(request, "company_domain", required=True)
        assert domain is not None
        return normalize_pipedrive_api_domain(domain)
    except PipedriveCredentialConfigurationError as exc:
        raise ValidationError(str(exc)) from exc


def _headers(request: ConnectorRequest) -> dict[str, str]:
    auth_method_key = request.auth.method if request.auth is not None else None
    if auth_method_key == "api_token":
        return {"x-api-token": credential_value(request, "api_token")}
    if auth_method_key in {"oauth2_authorization_code", "oauth2_token"}:
        return {"Authorization": f"Bearer {credential_value(request, 'access_token')}"}
    raise ValidationError(
        "Pipedrive action requires a recognized saved auth method",
        data={"auth_method_key": auth_method_key},
    )


def _list_params(request: ConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {}
    key_map = {
        "filter_id": "filter_id",
        "owner_id": "owner_id",
        "person_id": "person_id",
        "organization_id": "org_id",
        "pipeline_id": "pipeline_id",
        "stage_id": "stage_id",
        "status": "status",
        "updated_since": "updated_since",
        "updated_until": "updated_until",
        "sort_by": "sort_by",
        "sort_direction": "sort_direction",
        "include_fields": "include_fields",
        "custom_fields": "custom_fields",
        "limit": "limit",
        "cursor": "cursor",
    }
    for source, target in key_map.items():
        value = payload.get(source)
        if value is None:
            continue
        params[target] = value
    return params


def _search_params(request: ConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    term = payload.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ValidationError("Pipedrive deal search requires term")
    params: dict[str, Any] = {"term": term}
    if "organization_id" in payload:
        params["organization_id"] = payload["organization_id"]
    if "person_id" in payload:
        params["person_id"] = payload["person_id"]
    for key in ("status", "include_fields", "limit", "cursor"):
        if payload.get(key) is not None:
            params[key] = payload[key]
    if "exact_match" in payload:
        params["exact_match"] = bool(payload["exact_match"])
    if "fields" in payload:
        fields = payload["fields"]
        params["fields"] = ",".join(fields) if isinstance(fields, list) else str(fields)
    return params


class PipedriveActionConnector:
    """Decision-free adapter for Pipedrive deals read/search endpoints."""

    key = "pipedrive"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        match request.operation:
            case "deals.list":
                optional_str(payload, "status", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=500)
            case "deals.search":
                optional_str(payload, "status", issues)
                optional_str(payload, "term", issues)
                if not payload.get("term"):
                    issues.append(
                        ValidationIssue(
                            path="$.term",
                            message="term is required",
                            code="required",
                        )
                    )
                int_range(payload, "limit", issues, minimum=1, maximum=500)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        match request.operation:
            case "deals.list":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{_base_url(request)}/api/v2/deals",
                    headers=_headers(request),
                    params=_list_params(request),
                )
            case "deals.search":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{_base_url(request)}/api/v2/deals/search",
                    headers=_headers(request),
                    params=_search_params(request),
                )
            case _:
                raise ValidationError(f"unsupported Pipedrive operation {request.operation!r}")
        return result(
            provider="pipedrive",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["PipedriveActionConnector"]
