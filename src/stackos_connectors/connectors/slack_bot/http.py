"""Slack HTTP protocol. Each side-effecting request is attempted once."""

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

import httpx

from stackos_connectors.contracts import ConnectorRequest
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.redaction import redact_secret_text
from stackos_connectors.shared.provider_utils import credential_config, credential_value

from .constants import _BASE_URL, _SLACK_TOKEN_RE


async def _request(request: ConnectorRequest, method: str, url: str, **kwargs):
    timeout = request.options.timeout
    owner = (
        nullcontext(request.options.http)
        if request.options.http is not None
        else httpx.AsyncClient(timeout=timeout if timeout is not None else 60.0)
    )
    async with owner as http:
        if timeout is not None:
            kwargs["timeout"] = timeout
        return await http.request(method, url, **kwargs)


async def slack_api(
    request: ConnectorRequest,
    method: str,
    api_method: str,
    *,
    json_body: Mapping[str, Any] | None = None,
    form_body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
):
    if json_body is not None and form_body is not None:
        raise ValidationError("Slack request cannot mix JSON and form bodies")
    config = credential_config(request)
    base = str(config.get("api_base_url") or _BASE_URL).rstrip("/")
    token = credential_value(request, "bot_token", "access_token", "token")
    headers = {"Authorization": f"Bearer {token}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    mutation = api_method not in {
        "auth.test",
        "conversations.info",
        "conversations.list",
        "conversations.members",
        "conversations.history",
    }
    try:
        response = await _request(
            request,
            method,
            f"{base}/{api_method}",
            headers=headers,
            json=dict(json_body) if json_body is not None else None,
            data=dict(form_body) if form_body is not None else None,
            params=dict(params or {}),
        )
    except httpx.TransportError as exc:
        raise ConnectorError(
            "Slack request outcome is unknown" if mutation else "Slack request failed",
            provider_error={"error_type": type(exc).__name__},
            metadata_json={
                "provider_executed": True,
                "outcome_unknown": mutation,
                "retry_safe": not mutation,
                "slack_method": api_method,
            },
        ) from exc
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.status_code < 400 and (
        not isinstance(body, Mapping) or not isinstance(body.get("ok"), bool)
    ):
        raise ConnectorError(
            f"Slack {api_method} returned an incomplete protocol response",
            provider_status_code=response.status_code,
            metadata_json={
                "provider_executed": True,
                "outcome_unknown": mutation,
                "retry_safe": not mutation,
                "slack_method": api_method,
                "request_id": response.headers.get("x-slack-req-id"),
            },
        )
    if response.status_code >= 400 or (isinstance(body, Mapping) and body.get("ok") is False):
        detail = (
            _slack_error_message(api_method, body)
            if isinstance(body, Mapping) and body.get("ok") is False
            else _redact_slack_text(
                f"Slack {api_method} returned status {response.status_code}: {response.text[:500]}"
            )
        )
        raise ConnectorError(
            detail,
            provider_status_code=response.status_code,
            provider_error=body,
            metadata_json={
                "provider_executed": True,
                "slack_method": api_method,
                "request_id": response.headers.get("x-slack-req-id"),
                "retry_after": response.headers.get("retry-after"),
                "outcome_unknown": mutation and response.status_code >= 500,
                "retry_safe": not mutation,
            },
        )
    return response.status_code, body, response.headers


async def upload_bytes(
    request: ConnectorRequest, *, upload_url: str, content: bytes, mime_type: str
):
    try:
        response = await _request(
            request,
            "POST",
            upload_url,
            headers={"Content-Type": mime_type or "application/octet-stream"},
            content=content,
        )
    except httpx.TransportError as exc:
        raise ConnectorError(
            "Slack upload outcome is unknown",
            provider_error={"error_type": type(exc).__name__},
            metadata_json={"provider_executed": True, "outcome_unknown": True, "retry_safe": False},
        ) from exc
    if response.status_code >= 400:
        raise ConnectorError(
            "Slack upload failed",
            provider_status_code=response.status_code,
            metadata_json={
                "provider_executed": True,
                "outcome_unknown": response.status_code >= 500,
                "retry_safe": False,
            },
        )
    return response.status_code, response.headers


def _redact_slack_text(value: str) -> str:
    return _SLACK_TOKEN_RE.sub("[redacted]", redact_secret_text(value))


def _slack_error_message(api_method: str, body: Mapping[str, Any]) -> str:
    error = str(body.get("error") or "unknown_error")
    details = []
    for key in ("needed", "provided"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            details.append(f"{key}={value.strip()}")
    metadata = body.get("response_metadata")
    messages = metadata.get("messages") if isinstance(metadata, Mapping) else None
    if isinstance(messages, list):
        details.extend(str(item) for item in messages if isinstance(item, str) and item.strip())
    suffix = f" ({', '.join(details)})" if details else ""
    return _redact_slack_text(f"Slack {api_method} returned error {error}{suffix}")
