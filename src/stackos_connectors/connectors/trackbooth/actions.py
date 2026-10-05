"""Explicit Trackbooth catalog requests and immutable native route declarations."""

from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx

from stackos_connectors import ActionDefinition, AuthMethodDefinition, ConnectorResult
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared.provider_utils import credential_config, credential_value, issue

from .assets import TrackboothAssets, _detail_from_endpoint
from .integration import normalize_trackbooth_base_url, trackbooth_headers
from .schema import operation_input_schema
from .transport import (
    _extract_catalog_export,
    _extract_catalog_items,
    _extract_operation_detail,
    _serialize_query,
    _substitute_path_params,
)

AUTH_METHOD = AuthMethodDefinition(
    key="api-key",
    fields_schema={
        "type": "object",
        "required": ["api_key"],
        "properties": {"api_key": {"type": "string", "minLength": 1}},
        "additionalProperties": True,
    },
    config_schema={
        "type": "object",
        "properties": {"api_base_url": {"type": "string", "minLength": 1}},
        "additionalProperties": True,
    },
    description="Supply the resolved Trackbooth account API key and optional API base URL.",
)


def declare_action(key: str, endpoint: Mapping[str, Any]) -> ActionDefinition:
    """Declare one named route before execution; register it with client.register_actions."""
    method = str(endpoint.get("method") or "").upper()
    path = endpoint.get("path")
    operation_id = endpoint.get("operation_id")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValidationError("Trackbooth declaration requires a supported HTTP method")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValidationError("Trackbooth declaration requires a relative API path")
    if "?" in path or "#" in path:
        raise ValidationError("Trackbooth declaration path must exclude query and fragment")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValidationError("Trackbooth declaration requires an operation_id")
    detail = _detail_from_endpoint(endpoint, openapi_schemas=TrackboothAssets().openapi_schemas)
    return ActionDefinition(
        connector="trackbooth",
        key=key,
        operation="operation.execute",
        auth_methods=(AUTH_METHOD,),
        input_schema=operation_input_schema(detail),
        output_schema={"type": "object", "additionalProperties": True},
        config={
            "operation_id": operation_id,
            "method": method,
            "path": path,
            "has_body": detail.get("request_body") is not None,
            "response_schema": detail.get("response"),
        },
        description=str(detail.get("description") or detail.get("title") or operation_id),
        guidance=(
            "Pass this operation's path parameters, query and body. The registered route is fixed."
        ),
        metadata={"name": str(detail.get("title") or operation_id)},
    )


class TrackboothActionConnector:
    key = "trackbooth"

    def validate(self, request):
        if request.operation not in {
            "catalog.list",
            "catalog.export",
            "operation.describe",
            "operation.execute",
        }:
            return [issue("$.operation", "unsupported Trackbooth operation", "enum_mismatch")]
        try:
            normalize_trackbooth_base_url(credential_config(request).get("api_base_url"))
        except ValueError as exc:
            return [issue("$.auth.config.api_base_url", str(exc))]
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        base_url = normalize_trackbooth_base_url(credential_config(request).get("api_base_url"))
        acting = request.options.provider_context.get("acting_as_account")
        acting = acting.strip() if isinstance(acting, str) and acting.strip() else None
        headers = trackbooth_headers(credential_value(request, "api_key"), acting_as_account=acting)
        method = "GET"
        params = []
        body = None
        if request.operation == "catalog.list":
            path = "/api/agent-api/catalog"
        elif request.operation == "catalog.export":
            path = "/api/agent-api/catalog/export"
        elif request.operation == "operation.describe":
            path = "/api/agent-api/catalog/" + quote(
                request.input_json["operation_id"].strip(), safe=""
            )
        else:
            method = request.config_json["method"]
            path = _substitute_path_params(
                request.config_json["path"], request.input_json.get("path_params")
            )
            params = _serialize_query(request.input_json.get("query"))
            if request.config_json.get("has_body"):
                body = request.input_json.get("body")
        status, response, response_headers = await _request_json(
            request, method=method, url=base_url + path, headers=headers, params=params, body=body
        )
        metadata = {
            "vendor": "trackbooth",
            "operation": request.operation,
            "status_code": status,
            "provider_executed": True,
        }
        request_id = response_headers.get("x-request-id")
        if request_id:
            metadata["request_id"] = request_id
        try:
            if request.operation == "catalog.list":
                output = {"data": _extract_catalog_items(response)}
            elif request.operation == "catalog.export":
                output = {"data": _extract_catalog_export(response)}
            elif request.operation == "operation.describe":
                endpoint = _extract_operation_detail(response)
                endpoint.setdefault("operation_id", request.input_json["operation_id"].strip())
                output = {
                    "endpoint": endpoint,
                    "data": _detail_from_endpoint(
                        endpoint, openapi_schemas=TrackboothAssets().openapi_schemas
                    ),
                }
            else:
                output = {
                    "operation_id": request.config_json["operation_id"],
                    "method": method,
                    "path": request.config_json["path"],
                    "status_code": status,
                    "data": response,
                    "response_schema": thaw(request.config_json.get("response_schema")),
                }
                metadata["operation_id"] = request.config_json["operation_id"]
        except ConnectorError as exc:
            exc.metadata_json.update(metadata)
            exc.metadata_json["retry_safe"] = method == "GET"
            raise
        return ConnectorResult(output_json=output, metadata_json=metadata)


@asynccontextmanager
async def _http(request):
    if request.options.http is not None:
        yield request.options.http
    else:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            yield client


async def _request_json(request, *, method, url, headers, params, body=None):
    kwargs = {
        "method": method,
        "url": url,
        "headers": headers,
        "params": params,
        "follow_redirects": False,
    }
    if body is not None:
        kwargs["json"] = body
    if request.options.timeout is not None:
        kwargs["timeout"] = request.options.timeout
    try:
        async with _http(request) as http:
            response = await http.request(**kwargs)
    except httpx.HTTPError as exc:
        raise ConnectorError(
            "Trackbooth request outcome is unknown",
            metadata_json={
                "provider_executed": True,
                "outcome_unknown": True,
                "retry_safe": method == "GET",
            },
        ) from exc
    if response.status_code >= 400:
        try:
            provider_error = response.json()
        except ValueError:
            provider_error = {"message": response.text[:1000]}
        raise ConnectorError(
            f"Trackbooth returned status {response.status_code}",
            provider_status_code=response.status_code,
            provider_error=provider_error,
            metadata_json={
                "vendor": "trackbooth",
                "status_code": response.status_code,
                "provider_executed": True,
                "retry_safe": method == "GET",
                "outcome_unknown": response.status_code >= 500,
            },
        )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body, response.headers
