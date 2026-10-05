"""Meta Marketing API action connector.

Official docs verified:
- Marketing API overview: https://developers.facebook.com/docs/marketing-api/
- Campaign structure: https://developers.facebook.com/docs/marketing-api/campaign-structure/
- Meta Business SDK generated objects: https://github.com/facebook/facebook-python-business-sdk
- Conversions API: https://developers.facebook.com/docs/marketing-api/conversions-api/
"""

from __future__ import annotations

import json
from typing import Any

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    credential_value,
    dict_field,
    list_field,
    optional_str,
    q,
    required_str,
    result,
    send_json,
    unknown_operation,
)

_BASE_URL = "https://graph.facebook.com"
_DEFAULT_VERSION = "v25.0"


def _version(request: ConnectorRequest) -> str:
    from stackos_connectors.shared.provider_utils import config_str

    return config_str(request, "api_version", default=_DEFAULT_VERSION) or _DEFAULT_VERSION


def _auth_headers(request: ConnectorRequest) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential_value(request, 'access_token', 'token')}"}


def _jsonish_form(data: dict[str, Any]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, dict | list):
            rendered[key] = json.dumps(value, separators=(",", ":"))
        else:
            rendered[key] = str(value)
    return rendered


def _account_id(request: ConnectorRequest, account_id: str) -> str:
    resolved = str(account_id)
    return resolved if resolved.startswith("act_") else f"act_{resolved}"


def _body_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"Meta {key} must be an object")
    return dict(value)


def _insights_params(request: ConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {}
    fields = payload.get("fields")
    if isinstance(fields, list) and fields:
        params["fields"] = ",".join(str(field) for field in fields)
    time_range = payload.get("time_range")
    if isinstance(time_range, dict):
        params["time_range"] = json.dumps(time_range, separators=(",", ":"))
    breakdowns = payload.get("breakdowns")
    if isinstance(breakdowns, list) and breakdowns:
        params["breakdowns"] = ",".join(str(item) for item in breakdowns)
    for key in ("level", "date_preset", "time_increment", "limit", "after"):
        value = payload.get(key)
        if value is not None:
            params[key] = str(value)
    return params


class MetaAdsActionConnector:
    """Decision-free adapter for Meta Marketing API edges."""

    key = "meta-ads"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        # Validate native numeric IDs without changing their execution values.
        payload = dict(request.input_json)
        for key in (
            "account_id",
            "campaign_id",
            "ad_set_id",
            "creative_id",
            "ad_id",
            "scope_id",
            "dataset_id",
        ):
            if isinstance(payload.get(key), int) and not isinstance(payload[key], bool):
                payload[key] = str(payload[key])
        issues: list[ValidationIssue] = []
        match request.operation:
            case "account.list":
                list_field(payload, "fields", issues)
            case "campaign.create":
                required_str(payload, "account_id", issues)
                dict_field(payload, "campaign", issues, required=True)
            case "campaign.update":
                required_str(payload, "campaign_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case "campaign.pause" | "campaign.resume":
                required_str(payload, "campaign_id", issues)
            case "campaign_budget.update":
                required_str(payload, "campaign_id", issues)
                dict_field(payload, "budget", issues, required=True)
            case "ad_set.create":
                required_str(payload, "account_id", issues)
                required_str(payload, "campaign_id", issues)
                dict_field(payload, "ad_set", issues, required=True)
            case "ad_set.update":
                required_str(payload, "ad_set_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case "ad_set_budget.update":
                required_str(payload, "ad_set_id", issues)
                dict_field(payload, "budget", issues, required=True)
            case "ad_creative.create":
                required_str(payload, "account_id", issues)
                dict_field(payload, "creative", issues, required=True)
            case "ad.create":
                required_str(payload, "account_id", issues)
                required_str(payload, "ad_set_id", issues)
                required_str(payload, "creative_id", issues)
                dict_field(payload, "ad", issues, required=True)
            case "ad.update":
                required_str(payload, "ad_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case "insights.fetch":
                required_str(payload, "scope_id", issues)
                list_field(payload, "fields", issues, required=True)
                optional_str(payload, "level", issues)
                dict_field(payload, "time_range", issues)
            case "conversions.send":
                required_str(payload, "dataset_id", issues)
                list_field(payload, "events", issues, required=True, max_items=1000)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        headers = _auth_headers(request)
        version = _version(request)
        payload = request.input_json
        match request.operation:
            case "account.list":
                fields = payload.get("fields")
                params: dict[str, Any] = {
                    "fields": ",".join(fields)
                    if isinstance(fields, list) and fields
                    else "id,account_id,name,account_status,currency,timezone_name"
                }
                if isinstance(payload.get("limit"), int):
                    params["limit"] = str(payload["limit"])
                if payload.get("after"):
                    params["after"] = str(payload["after"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{_BASE_URL}/{version}/me/adaccounts",
                    headers=headers,
                    params=params,
                )
            case "campaign.create":
                campaign = _body_object(payload, "campaign")
                account_id = _account_id(request, str(payload["account_id"]))
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{account_id}/campaigns",
                    headers=headers,
                    data=_jsonish_form(campaign),
                )
            case "campaign.update":
                campaign_id = str(payload["campaign_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(campaign_id)}",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "changes")),
                )
            case "campaign.pause" | "campaign.resume":
                campaign_id = str(payload["campaign_id"])
                status_value = "PAUSED" if request.operation.endswith("pause") else "ACTIVE"
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(campaign_id)}",
                    headers=headers,
                    data={"status": status_value},
                )
            case "campaign_budget.update":
                campaign_id = str(payload["campaign_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(campaign_id)}",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "budget")),
                )
            case "ad_set.create":
                ad_set = _body_object(payload, "ad_set")
                ad_set["campaign_id"] = str(payload["campaign_id"])
                account_id = _account_id(request, str(payload["account_id"]))
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{account_id}/adsets",
                    headers=headers,
                    data=_jsonish_form(ad_set),
                )
            case "ad_set.update":
                ad_set_id = str(payload["ad_set_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(ad_set_id)}",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "changes")),
                )
            case "ad_set_budget.update":
                ad_set_id = str(payload["ad_set_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(ad_set_id)}",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "budget")),
                )
            case "ad_creative.create":
                account_id = _account_id(request, str(payload["account_id"]))
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{account_id}/adcreatives",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "creative")),
                )
            case "ad.create":
                ad = _body_object(payload, "ad")
                ad["adset_id"] = str(payload["ad_set_id"])
                creative_id = str(payload["creative_id"])
                ad["creative"] = {"creative_id": creative_id}
                account_id = _account_id(request, str(payload["account_id"]))
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{account_id}/ads",
                    headers=headers,
                    data=_jsonish_form(ad),
                )
            case "ad.update":
                ad_id = str(payload["ad_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(ad_id)}",
                    headers=headers,
                    data=_jsonish_form(_body_object(payload, "changes")),
                )
            case "insights.fetch":
                scope = str(payload["scope_id"])
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{_BASE_URL}/{version}/{q(scope)}/insights",
                    headers=headers,
                    params=_insights_params(request),
                )
            case "conversions.send":
                dataset_id = str(payload["dataset_id"])
                body_json = {
                    "data": payload["events"],
                    **(
                        {"test_event_code": payload["test_event_code"]}
                        if payload.get("test_event_code")
                        else {}
                    ),
                }
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{version}/{q(dataset_id)}/events",
                    headers={**headers, "Content-Type": "application/json"},
                    json_body=body_json,
                )
            case _:
                raise ValidationError(f"unsupported Meta operation {request.operation!r}")
        return result(
            provider="meta-ads",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["MetaAdsActionConnector"]
