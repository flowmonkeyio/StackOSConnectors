"""Taboola Backstage API action connector.

Official docs verified:
- Client credentials flow: https://developers.taboola.com/backstage-api/reference/client-credentials-flow
- Create campaign: https://developers.taboola.com/backstage-api/reference/create-a-campaign
- Update campaign: https://developers.taboola.com/backstage-api/reference/update-a-campaign
- Create campaign item: https://developers.taboola.com/backstage-api/reference/create-a-campaign-item
- Update campaign item: https://developers.taboola.com/backstage-api/reference/update-a-campaign-item
- Campaign summary report: https://developers.taboola.com/backstage-api/reference/campaign-summary-report
- Conversion rule quick reference: https://developers.taboola.com/backstage-api/reference/conversion-rule-quick-reference
- API reference index/OpenAPI: https://developers.taboola.com/llms.txt
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
    credential_payload,
    dict_field,
    issue,
    q,
    required_str,
    result,
    send_json,
    unknown_operation,
)

_BASE_URL = "https://backstage.taboola.com"


def _access_token(request: ConnectorRequest) -> str:
    payload = credential_payload(request)
    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    raise ValidationError("taboola credential requires a resolved access_token")


def _headers(request: ConnectorRequest) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token(request)}",
        "Content-Type": "application/json",
    }


def _account_id(request: ConnectorRequest) -> str:
    return str(request.input_json["account_id"])


def _campaign_id(request: ConnectorRequest) -> str:
    return str(request.input_json["campaign_id"])


def _item_id(request: ConnectorRequest) -> str:
    return str(request.input_json["item_id"])


def _rule_id(request: ConnectorRequest) -> str:
    return str(request.input_json["conversion_rule_id"])


def _body(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"taboola {key} must be an object")
    return value


def _validate_static_item(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    item = payload.get("item")
    if not isinstance(item, dict):
        return
    if set(item) != {"url"}:
        issues.append(
            issue(
                "$.item",
                "Taboola static item creation accepts exactly the url field",
                "schema_mismatch",
            )
        )
    elif not isinstance(item.get("url"), str) or not item["url"].strip():
        issues.append(issue("$.item.url", "item.url is required", "required"))


def _validate_report_filters(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    exclusive = [key for key in ("platform", "country", "site", "partner_name") if payload.get(key)]
    if len(exclusive) > 1:
        issues.append(
            issue(
                "$",
                (
                    "Taboola report filters platform, country, site, and partner_name "
                    "are mutually exclusive"
                ),
                "validation_error",
            )
        )


class TaboolaActionConnector:
    """Decision-free adapter for Taboola account-scoped Backstage endpoints."""

    key = "taboola"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        # Validate native numeric IDs without changing their execution values.
        payload = dict(request.input_json)
        for key in ("account_id", "campaign_id", "item_id", "conversion_rule_id"):
            if isinstance(payload.get(key), int) and not isinstance(payload[key], bool):
                payload[key] = str(payload[key])
        issues: list[ValidationIssue] = []
        match request.operation:
            case "account.get":
                pass
            case "campaign.create":
                required_str(payload, "account_id", issues)
                dict_field(payload, "campaign", issues, required=True)
            case "campaign.update":
                required_str(payload, "account_id", issues)
                required_str(payload, "campaign_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case "campaign.pause" | "campaign.resume":
                required_str(payload, "account_id", issues)
                required_str(payload, "campaign_id", issues)
            case "item.create":
                required_str(payload, "account_id", issues)
                required_str(payload, "campaign_id", issues)
                dict_field(payload, "item", issues, required=True)
                _validate_static_item(payload, issues)
            case "item.update":
                required_str(payload, "account_id", issues)
                required_str(payload, "campaign_id", issues)
                required_str(payload, "item_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case "report.fetch":
                required_str(payload, "account_id", issues)
                required_str(payload, "dimension", issues)
                required_str(payload, "start_date", issues)
                required_str(payload, "end_date", issues)
                _validate_report_filters(payload, issues)
            case "conversion_rule.create":
                required_str(payload, "account_id", issues)
                dict_field(payload, "conversion_rule", issues, required=True)
            case "conversion_rule.update":
                required_str(payload, "account_id", issues)
                required_str(payload, "conversion_rule_id", issues)
                dict_field(payload, "changes", issues, required=True)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        headers = _headers(request)
        account_id = _account_id(request) if request.operation != "account.get" else None
        base = f"{_BASE_URL}/backstage/api/1.0"
        payload = request.input_json
        match request.operation:
            case "account.get":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{base}/users/current/account",
                    headers=headers,
                )
            case "campaign.create":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/",
                    headers=headers,
                    json_body=_body(payload, "campaign"),
                )
            case "campaign.update":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="PUT",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case "campaign.pause" | "campaign.resume":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="PUT",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}",
                    headers=headers,
                    json_body={"is_active": request.operation.endswith("resume")},
                )
            case "item.create":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}/items/",
                    headers=headers,
                    json_body=_body(payload, "item"),
                )
            case "item.update":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}/items/{q(_item_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case "report.fetch":
                params = {
                    "start_date": payload["start_date"],
                    "end_date": payload["end_date"],
                }
                if payload.get("campaign_id") is not None:
                    params["campaign"] = payload["campaign_id"]
                for key in ("site", "platform", "country", "partner_name"):
                    if payload.get(key) is not None:
                        params[key] = payload[key]
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="GET",
                    url=f"{base}/{q(account_id)}/reports/campaign-summary/dimensions/{q(payload['dimension'])}",
                    headers=headers,
                    params=params,
                )
            case "conversion_rule.create":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{base}/{q(account_id)}/universal_pixel/conversion_rule",
                    headers=headers,
                    json_body=_body(payload, "conversion_rule"),
                )
            case "conversion_rule.update":
                status, body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="PUT",
                    url=f"{base}/{q(account_id)}/universal_pixel/conversion_rule/{q(_rule_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case _:
                raise ValidationError(f"unsupported Taboola operation {request.operation!r}")
        return result(
            provider="taboola",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["TaboolaActionConnector"]
