"""Shared no-secret helpers for provider-specific action connectors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import (
    ConnectorError,
    IntegrationDownError,
    RateLimitedError,
    ValidationError,
)
from stackos_connectors.redaction import redact_secret_text, redact_secret_values, redact_secrets

JsonObject = dict[str, Any]


def issue(path: str, message: str, code: str = "validation_error") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, code=code)


def unknown_operation(request: ConnectorRequest) -> list[ValidationIssue]:
    return [issue("$.operation", f"unsupported operation {request.operation!r}", "enum_mismatch")]


def required_str(payload: Mapping[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))


def optional_str(payload: Mapping[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(issue(f"$.{key}", f"{key} must be a string", "type_error"))


def dict_field(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, dict):
        issues.append(issue(f"$.{key}", f"{key} must be an object", "type_error"))


def list_field(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
    max_items: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list):
        issues.append(issue(f"$.{key}", f"{key} must be an array", "type_error"))
        return
    if max_items is not None and len(value) > max_items:
        issues.append(issue(f"$.{key}", f"{key} must contain at most {max_items} items", "length"))


def int_range(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        issues.append(
            issue(f"$.{key}", f"{key} must be an integer between {minimum} and {maximum}", "range")
        )


def credential_payload(request: ConnectorRequest) -> JsonObject:
    if request.auth is None:
        raise ValidationError(f"{request.connector} requires authentication")
    from stackos_connectors.contracts import thaw

    return thaw(request.auth.fields)


def credential_config(request: ConnectorRequest) -> JsonObject:
    from stackos_connectors.contracts import thaw

    return thaw(request.auth.config) if request.auth is not None else {}


def credential_value(request: ConnectorRequest, *keys: str) -> str:
    payload = credential_payload(request)
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    value = payload.get("value")
    if value is not None and str(value).strip():
        return str(value).strip()
    raise ValidationError(f"{request.connector} credential missing token")


def bearer_headers(request: ConnectorRequest, *keys: str) -> dict[str, str]:
    token_keys = keys or ("access_token", "api_key", "bearer_token", "token")
    return {
        "Authorization": f"Bearer {credential_value(request, *token_keys)}",
        "Content-Type": "application/json",
    }


def header_token_headers(
    request: ConnectorRequest,
    *,
    header_name: str,
    keys: tuple[str, ...] = ("api_key", "token", "value"),
) -> dict[str, str]:
    return {
        header_name: credential_value(request, *keys),
        "Content-Type": "application/json",
    }


def basic_auth(request: ConnectorRequest) -> httpx.BasicAuth:
    payload = credential_payload(request)
    username = str(payload.get("username") or payload.get("user") or "")
    password = str(payload.get("password") or payload.get("secret") or "")
    raw = payload.get("value")
    if (not username or not password) and isinstance(raw, str) and ":" in raw:
        username, password = raw.split(":", 1)
    if not username or not password:
        raise ValidationError("basic credential missing username/password")
    return httpx.BasicAuth(username, password)


def connector_error_from_integration(
    exc: IntegrationDownError | RateLimitedError,
    *,
    provider: str,
    operation: str,
) -> ConnectorError:
    """Translate integration-wrapper failures into action audit output."""
    data = exc.data if isinstance(exc.data, dict) else {}
    status = data.get("status")
    try:
        provider_status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        provider_status_code = None
    provider_error = data.get("provider_error")
    if provider_error is None:
        provider_error = {"message": redact_secret_text(exc.detail)}
    metadata: JsonObject = {"vendor": provider, "operation": operation}
    if provider_status_code is not None:
        metadata["status_code"] = provider_status_code
    retry_after = data.get("retry_after")
    if retry_after is not None:
        metadata["retry_after"] = retry_after
    return ConnectorError(
        redact_secret_text(exc.detail),
        provider_status_code=provider_status_code,
        provider_error=redact_secrets(provider_error),
        metadata_json=metadata,
    )


def config_str(
    request: ConnectorRequest,
    key: str,
    *,
    default: str | None = None,
    required: bool = False,
) -> str | None:
    value = credential_config(request).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        target = request.connector
        raise ValidationError(f"{target} credential missing {key}")
    return default


def nested_value(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return current


def clean_customer_id(value: Any) -> str:
    return str(value).replace("-", "").strip()


def q(value: Any) -> str:
    return quote(str(value), safe="")


async def send_json(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    json_body: Any = None,
    data: Mapping[str, Any] | None = None,
    auth: httpx.Auth | None = None,
    timeout_s: float = 60.0,
    redact_values: tuple[str, ...] = (),
    http: httpx.AsyncClient | None = None,
) -> tuple[int, Any, httpx.Headers]:
    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": dict(headers or {}),
        "params": dict(params or {}),
        "auth": auth,
    }
    if json_body is not None:
        kwargs["json"] = json_body
    if data is not None:
        kwargs["data"] = data
    if http is None:
        async with httpx.AsyncClient(timeout=timeout_s) as owned_http:
            response = await owned_http.request(**kwargs)
    else:
        response = await http.request(**kwargs, timeout=timeout_s)
    if response.status_code >= 400:
        try:
            provider_error: Any = response.json()
        except ValueError:
            provider_error = {"message": response.text[:500]}
        header_secrets: list[str] = []
        for header_name, header_value in (headers or {}).items():
            normalized_name = str(header_name).lower().replace("-", "_")
            if any(
                part in normalized_name for part in ("authorization", "api_key", "secret", "token")
            ):
                value = str(header_value).strip()
                if value.lower().startswith("bearer "):
                    value = value[7:].strip()
                if value:
                    header_secrets.append(value)
        provider_error = redact_secret_values(
            provider_error,
            tuple(header_secrets) + tuple(value for value in redact_values if value),
        )
        metadata: JsonObject = {"status_code": response.status_code}
        request_id = (
            response.headers.get("x-hubspot-correlation-id")
            or response.headers.get("request-id")
            or response.headers.get("google-ads-request-id")
            or response.headers.get("x-request-id")
        )
        if request_id:
            metadata["request_id"] = request_id
        retry_after = response.headers.get("retry-after")
        if retry_after:
            metadata["retry_after"] = retry_after
        raise ConnectorError(
            f"provider action returned status {response.status_code}",
            provider_status_code=response.status_code,
            provider_error=redact_secrets(provider_error),
            metadata_json=metadata,
        )
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body, response.headers


def result(
    *,
    provider: str,
    operation: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str] | None = None,
    metadata: JsonObject | None = None,
) -> ConnectorResult:
    meta: JsonObject = {"vendor": provider, "operation": operation, "status_code": status_code}
    if headers is not None:
        request_id = (
            headers.get("request-id")
            or headers.get("x-hubspot-correlation-id")
            or headers.get("google-ads-request-id")
            or headers.get("x-request-id")
            or headers.get("fbtrace_id")
        )
        if request_id:
            meta["request_id"] = request_id
    if metadata:
        meta.update(metadata)
    return ConnectorResult(
        output_json={
            "provider": provider,
            "operation": operation,
            "status_code": status_code,
            "body": body,
        },
        metadata_json=meta,
    )


__all__ = [
    "JsonObject",
    "basic_auth",
    "bearer_headers",
    "clean_customer_id",
    "config_str",
    "credential_config",
    "credential_payload",
    "credential_value",
    "dict_field",
    "header_token_headers",
    "int_range",
    "issue",
    "list_field",
    "nested_value",
    "optional_str",
    "q",
    "required_str",
    "result",
    "send_json",
    "unknown_operation",
]
