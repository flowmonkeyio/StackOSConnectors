"""Named HubSpot provider operations with native IDs, fields and resolved auth."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import quote

from stackos_connectors import ConnectorResult, ValidationError
from stackos_connectors.shared.provider_utils import bearer_headers, send_json

from .contract import HUBSPOT_ACTION_SPECS

_CAMPAIGN_PROPERTIES = {
    "name": "hs_name",
    "start_date": "hs_start_date",
    "end_date": "hs_end_date",
    "notes": "hs_notes",
    "audience": "hs_audience",
    "currency_code": "hs_currency_code",
    "status": "hs_campaign_status",
    "utm": "hs_utm",
}


_CAMPAIGN_STATUSES = {"planned", "in_progress", "active", "paused", "completed"}


def _validated_date(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"HubSpot {field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"HubSpot {field} must use YYYY-MM-DD") from exc
    return value


def _campaign_write_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for input_key, provider_key in _CAMPAIGN_PROPERTIES.items():
        if payload.get(input_key) is not None:
            properties[provider_key] = payload[input_key]
    for date_key in ("start_date", "end_date"):
        if date_key in payload:
            properties[_CAMPAIGN_PROPERTIES[date_key]] = _validated_date(
                payload.get(date_key),
                field=date_key,
            )
    status = payload.get("status")
    if status is not None and status not in _CAMPAIGN_STATUSES:
        raise ValidationError("HubSpot campaign status is not supported")
    currency = payload.get("currency_code")
    if currency is not None:
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
            raise ValidationError("HubSpot campaign currency_code must be a 3-letter code")
        properties["hs_currency_code"] = currency.upper()
    start_date = properties.get("hs_start_date")
    end_date = properties.get("hs_end_date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValidationError("HubSpot campaign start_date must not follow end_date")
    if not properties:
        raise ValidationError("HubSpot campaign update requires at least one field")
    return {"properties": properties}


def _marketing_event_write_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "externalAccountId": payload["external_account_key"],
        "externalEventId": payload["external_event_key"],
        "eventName": payload["name"],
        "eventOrganizer": payload["organizer"],
    }
    for input_key, provider_key in (
        ("event_type", "eventType"),
        ("description", "eventDescription"),
        ("event_url", "eventUrl"),
        ("start_at", "startDateTime"),
        ("end_at", "endDateTime"),
        ("event_cancelled", "eventCancelled"),
        ("event_completed", "eventCompleted"),
    ):
        if payload.get(input_key) is not None:
            body[provider_key] = payload[input_key]
    custom_properties = payload.get("custom_properties")
    if isinstance(custom_properties, Mapping):
        body["customProperties"] = [
            {"name": str(name), "value": value} for name, value in sorted(custom_properties.items())
        ]
    return {"inputs": [body]}


class HubSpotActionConnector:
    key = "hubspot"

    def validate(self, request):
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        spec = HUBSPOT_ACTION_SPECS[request.operation]
        path_keys = re.findall(r"\{([^}]+)\}", spec["path"])
        path = spec["path"].format(
            **{k: quote(str(request.input_json[k]), safe="") for k in path_keys}
        )
        values = {k: v for k, v in request.input_json.items() if k not in path_keys}
        if spec["query"]:
            values = {
                k: ("true" if v else "false") if isinstance(v, bool) else v
                for k, v in values.items()
            }
        if request.operation in {"marketing.campaigns.create", "marketing.campaigns.update"}:
            body = _campaign_write_body(values)
        elif request.operation == "marketing.events.upsert":
            body = _marketing_event_write_body(values)
        else:
            body = values[spec["body_field"]] if spec["body_field"] else values
        status, response, headers = await send_json(
            method=spec["method"],
            url="https://api.hubapi.com" + path,
            headers=bearer_headers(request, "access_token"),
            params=values if spec["query"] else None,
            json_body=body if spec["has_body"] else None,
            http=request.options.http,
            timeout_s=request.options.timeout if request.options.timeout is not None else 60.0,
        )
        metadata = {
            "vendor": "hubspot",
            "operation": request.operation,
            "status_code": status,
            "provider_executed": True,
        }
        if headers.get("x-hubspot-correlation-id"):
            metadata["request_id"] = headers["x-hubspot-correlation-id"]
        return ConnectorResult(
            output_json={"status_code": status, "body": response, "headers": dict(headers)},
            metadata_json=metadata,
        )
