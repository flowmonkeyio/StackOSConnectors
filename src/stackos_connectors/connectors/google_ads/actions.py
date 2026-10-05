"""Google Ads API action connector.

Official docs verified:
- REST auth and required headers: https://developers.google.com/google-ads/api/rest/auth
- Mutate semantics: https://developers.google.com/google-ads/api/rest/common/mutate
- Search semantics: https://developers.google.com/google-ads/api/rest/common/search
- Offline click conversions: https://developers.google.com/google-ads/api/docs/conversions/upload-clicks
- Current release notes: https://developers.google.com/google-ads/api/docs/release-notes
"""

from __future__ import annotations

from typing import Any

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    clean_customer_id,
    config_str,
    credential_config,
    credential_payload,
    dict_field,
    list_field,
    required_str,
    result,
    send_json,
    unknown_operation,
)

_BASE_URL = "https://googleads.googleapis.com"
_DEFAULT_VERSION = "v24"

_MUTATE_RESOURCE_BY_OPERATION = {
    "campaign_budget.create": "campaignBudgets",
    "campaign_budget.update": "campaignBudgets",
    "campaign.create": "campaigns",
    "ad_group.create": "adGroups",
    "asset.create": "assets",
    "ad_group_ad.create": "adGroupAds",
    "conversion_action.create": "conversionActions",
}
_PAYLOAD_KEY_BY_OPERATION = {
    "campaign_budget.create": "budget",
    "campaign_budget.update": "changes",
    "campaign.create": "campaign",
    "ad_group.create": "ad_group",
    "asset.create": "asset",
    "ad_group_ad.create": "ad_group_ad",
    "conversion_action.create": "conversion_action",
}


def _access_token(request: ConnectorRequest) -> str:
    payload = credential_payload(request)
    token = payload.get("access_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    raise ValidationError(
        "google-ads credential missing access_token; reconnect or renew the credential"
    )


def _headers(request: ConnectorRequest) -> dict[str, str]:
    payload = credential_payload(request)
    developer_token = payload.get("developer_token")
    if not isinstance(developer_token, str) or not developer_token.strip():
        raise ValidationError("google-ads credential missing developer_token")
    headers = {
        "Authorization": f"Bearer {_access_token(request)}",
        "developer-token": developer_token.strip(),
        "Content-Type": "application/json",
    }
    config = credential_config(request)
    login_customer_id = config.get("login_customer_id") or payload.get("login_customer_id")
    if login_customer_id:
        headers["login-customer-id"] = clean_customer_id(login_customer_id)
    return headers


def _version(request: ConnectorRequest) -> str:
    return config_str(request, "api_version", default=_DEFAULT_VERSION) or _DEFAULT_VERSION


def _customer_id(request: ConnectorRequest) -> str:
    return clean_customer_id(request.input_json["customer_id"])


def _mutate_body(request: ConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    body_key = _PAYLOAD_KEY_BY_OPERATION[request.operation]
    operation_kind = "update" if request.operation.endswith(".update") else "create"
    body_obj = payload.get(body_key)
    if not isinstance(body_obj, dict):
        raise ValidationError(f"google-ads {body_key} must be an object")
    body_obj = dict(body_obj)
    customer_id = _customer_id(request)
    if request.operation == "campaign.create" and "campaign_budget_id" in payload:
        budget_id = payload["campaign_budget_id"]
        budget_resource = f"customers/{customer_id}/campaignBudgets/{budget_id}"
        body_obj.setdefault("campaignBudget", budget_resource)
    if request.operation == "campaign_budget.update" and "campaign_budget_id" in payload:
        budget_id = payload["campaign_budget_id"]
        body_obj.setdefault("resourceName", f"customers/{customer_id}/campaignBudgets/{budget_id}")
    if request.operation == "ad_group.create" and "campaign_id" in payload:
        campaign_id = payload["campaign_id"]
        body_obj.setdefault("campaign", f"customers/{customer_id}/campaigns/{campaign_id}")
    if request.operation == "ad_group_ad.create" and "ad_group_id" in payload:
        ad_group_id = payload["ad_group_id"]
        body_obj.setdefault("adGroup", f"customers/{customer_id}/adGroups/{ad_group_id}")
    operation: dict[str, Any] = {operation_kind: body_obj}
    if operation_kind == "update":
        update_mask = payload.get("update_mask") or payload.get("updateMask")
        if not isinstance(update_mask, str) or not update_mask.strip():
            raise ValidationError("google-ads update requires update_mask")
        operation["updateMask"] = update_mask
    rendered: dict[str, Any] = {"operations": [operation]}
    for key in ("partialFailure", "partial_failure", "validateOnly", "validate_only"):
        value = payload.get(key)
        if value is not None:
            rendered[_camel_flag(key)] = bool(value)
    return rendered


def _camel_flag(key: str) -> str:
    return {
        "debug_enabled": "debugEnabled",
        "job_id": "jobId",
        "partial_failure": "partialFailure",
        "validate_only": "validateOnly",
    }.get(key, key)


class GoogleAdsActionConnector:
    """Decision-free adapter for Google Ads REST APIs."""

    key = "google-ads"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        # Validate native numeric IDs without changing their execution values.
        payload = dict(request.input_json)
        if isinstance(payload.get("customer_id"), int) and not isinstance(
            payload["customer_id"], bool
        ):
            payload["customer_id"] = str(payload["customer_id"])
        issues: list[ValidationIssue] = []
        match request.operation:
            case "customer.list":
                pass
            case (
                "campaign_budget.create"
                | "campaign_budget.update"
                | "campaign.create"
                | "ad_group.create"
                | "asset.create"
                | "ad_group_ad.create"
                | "conversion_action.create"
            ):
                required_str(payload, "customer_id", issues)
                dict_field(
                    payload,
                    _PAYLOAD_KEY_BY_OPERATION[request.operation],
                    issues,
                    required=True,
                )
                if request.operation.endswith(".update"):
                    required_str(payload, "update_mask", issues)
            case "report.search":
                required_str(payload, "customer_id", issues)
                required_str(payload, "query", issues)
            case "conversion_upload.clicks":
                required_str(payload, "customer_id", issues)
                list_field(payload, "conversions", issues, required=True, max_items=2000)
                if payload.get("partial_failure") is False:
                    issues.append(
                        ValidationIssue(
                            path="$.partial_failure",
                            message=(
                                "Google Ads click conversion upload requires partial_failure=true"
                            ),
                            code="validation_error",
                        )
                    )
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        headers = _headers(request)
        version = _version(request)
        payload = request.input_json
        match request.operation:
            case "customer.list":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{_BASE_URL}/{version}/customers:listAccessibleCustomers",
                    headers=headers,
                )
            case (
                "campaign_budget.create"
                | "campaign_budget.update"
                | "campaign.create"
                | "ad_group.create"
                | "asset.create"
                | "ad_group_ad.create"
                | "conversion_action.create"
            ):
                resource = _MUTATE_RESOURCE_BY_OPERATION[request.operation]
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}/{resource}:mutate",
                    headers=headers,
                    json_body=_mutate_body(request),
                )
            case "report.search":
                body_json: dict[str, Any] = {"query": payload["query"]}
                if payload.get("page_cursor"):
                    body_json["pageToken"] = payload["page_cursor"]
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}/googleAds:search",
                    headers=headers,
                    json_body=body_json,
                )
                if isinstance(body, dict):
                    # Nonsecret paging data must survive the shared token-key redactor.
                    # https://developers.google.com/google-ads/api/rest/common/search
                    body = dict(body)
                    next_page = body.pop("nextPageToken", None)
                    if next_page:
                        body["next_page_cursor"] = next_page
            case "conversion_upload.clicks":
                body_json = {
                    "conversions": payload["conversions"],
                    "partialFailure": True,
                }
                for key in ("job_id", "debug_enabled", "validate_only"):
                    if key in payload:
                        body_json[_camel_flag(key)] = payload[key]
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}:uploadClickConversions",
                    headers=headers,
                    json_body=body_json,
                )
            case _:
                raise ValidationError(f"unsupported Google Ads operation {request.operation!r}")
        return result(
            provider="google-ads",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["GoogleAdsActionConnector"]
