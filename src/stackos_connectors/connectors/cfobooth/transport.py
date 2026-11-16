"""CFO transport policy over the shared bounded, no-replay HTTP owner."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared.http import NO_BODY, request_json
from stackos_connectors.validation import schema_issues

from .config import path_value


def _check_organization(value: Any, org: str) -> None:
    if isinstance(value, Mapping):
        for name, child in value.items():
            if name in {"organizationId", "organization_id"} and child != org:
                raise ValidationError("CFOBooth caller organization conflicts with Account binding")
            _check_organization(child, org)
    elif isinstance(value, list | tuple):
        for child in value:
            _check_organization(child, org)


def operation_payload(request, org: str) -> tuple[str, str, list[tuple[str, str]], Any]:
    descriptor = request.config_json["descriptor"]
    route, schemas = descriptor["route"], descriptor["schemas"]
    data = request.input_json
    _check_organization(data, org)
    params = {**data.get("path_params", {}), "organizationId": org}
    if schema_issues(schemas["params"], params):
        raise ValidationError("CFOBooth path parameters conflict with canonical binding")
    path = route["path"]
    for name, value in params.items():
        encoded = quote(path_value(value), safe="")
        path = re.sub(r":" + re.escape(name) + r"(?=/|$)", lambda _, encoded=encoded: encoded, path)
    query = []
    for name, value in data.get("query", {}).items():
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            if not isinstance(item, str | int | float | bool):
                raise ValidationError("CFOBooth query values must be scalar or scalar arrays")
            query.append((name, str(item).lower() if isinstance(item, bool) else str(item)))
    return route["method"], path, query, data.get("body", NO_BODY)


def response_error(status: int, method: str, reason: str) -> ConnectorError:
    return ConnectorError(
        "CFOBooth returned an invalid response",
        provider_status_code=status,
        metadata_json={
            "vendor": "cfobooth",
            "provider_executed": True,
            "status_code": status,
            "reason_code": reason,
            "outcome_unknown": True,
            "retry_safe": method == "GET",
        },
    )


def _safe_error(exc: ConnectorError, descriptor: Mapping | None) -> ConnectorError:
    error = exc.provider_error
    safe = None
    if (
        descriptor is not None
        and isinstance(error, dict)
        and set(error) == {"requestId", "error"}
        and isinstance(error["requestId"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", error["requestId"])
        and isinstance(error["error"], dict)
        and set(error["error"]) == {"code", "message"}
        and isinstance(error["error"]["code"], str)
        and re.fullmatch(r"[a-z_]{1,64}", error["error"]["code"])
        and isinstance(error["error"]["message"], str)
        and len(error["error"]["message"]) <= 160
        and not schema_issues(descriptor["schemas"]["error"], error)
    ):
        safe = error
    return ConnectorError(
        exc.detail,
        provider_status_code=exc.provider_status_code,
        provider_error=safe,
        metadata_json=exc.metadata_json,
    )


async def execute_request(
    request,
    *,
    method: str,
    url: str,
    key: str,
    params: list[tuple[str, str]],
    body: Any = NO_BODY,
    descriptor: Mapping | None = None,
) -> tuple[int, dict, dict]:
    if request.options.rate_limiter is not None:
        await request.options.rate_limiter.acquire(1)
    try:
        status, output, headers = await request_json(
            request,
            vendor="cfobooth",
            method=method,
            url=url,
            headers={
                "X-API-Key": key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            params=params,
            body=body,
            max_response_bytes=descriptor["responseLimit"] if descriptor else 16 * 1024 * 1024,
        )
    except ConnectorError as exc:
        raise _safe_error(exc, descriptor) from None
    if status < 200 or status >= 300 or not isinstance(output, dict):
        raise response_error(status, method, "invalid_json_or_status")
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise response_error(status, method, "invalid_content_type")
    if descriptor is None and status != 200:
        raise response_error(status, method, "invalid_setup_status")
    if descriptor is not None and (
        status != descriptor["route"]["status"]
        or schema_issues(descriptor["schemas"]["result"], output)
    ):
        raise response_error(status, method, "invalid_operation_response")
    return (
        status,
        output,
        {
            "vendor": "cfobooth",
            "status_code": status,
            "provider_executed": True,
            **(
                {"request_id": output["requestId"]}
                if isinstance(output.get("requestId"), str)
                else {}
            ),
        },
    )
