"""One bounded request, without replay, credential policy or client ownership transfer."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from stackos_connectors.contracts import ConnectorRequest
from stackos_connectors.errors import ConnectorError

NO_BODY = object()
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@asynccontextmanager
async def _http(request: ConnectorRequest) -> AsyncIterator[httpx.AsyncClient]:
    if request.options.http is not None:
        yield request.options.http
    else:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            yield client


async def _read_response(response: httpx.Response, *, max_bytes: int) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > max_bytes:
            raise ConnectorError("provider response exceeds byte limit")
        content.extend(chunk)
    return bytes(content)


async def request_json(
    request: ConnectorRequest,
    *,
    vendor: str,
    method: str,
    url: str,
    headers: Mapping[str, str],
    params: Sequence[tuple[str, str]],
    body: Any = NO_BODY,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, Any, httpx.Headers]:
    """Preserve JSON/text and outcome facts; providers select URL, auth and envelopes."""
    if max_response_bytes <= 0:
        raise ValueError("response byte limit must be positive")
    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": headers,
        "params": params,
        "follow_redirects": False,
    }
    if body is None:
        # httpx treats json=None as no body. A declared null is still JSON.
        kwargs["content"] = b"null"
        content_headers = httpx.Headers(headers)
        content_headers.setdefault("content-type", "application/json")
        kwargs["headers"] = content_headers
    elif body is not NO_BODY:
        kwargs["json"] = body
    if request.options.timeout is not None:
        kwargs["timeout"] = request.options.timeout
    try:
        async with _http(request) as http, http.stream(**kwargs) as response:
            try:
                raw = await _read_response(response, max_bytes=max_response_bytes)
            except ConnectorError as exc:
                exc.metadata_json.update(
                    {
                        "vendor": vendor,
                        "status_code": response.status_code,
                        "provider_executed": True,
                        "outcome_unknown": True,
                        "retry_safe": method == "GET",
                    }
                )
                raise
    except httpx.HTTPError as exc:
        raise ConnectorError(
            f"{vendor} request outcome is unknown",
            metadata_json={
                "provider_executed": True,
                "outcome_unknown": True,
                "retry_safe": method == "GET",
            },
        ) from exc
    try:
        output = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        output = raw.decode(response.encoding or "utf-8", errors="replace")
    if response.status_code >= 400:
        raise ConnectorError(
            f"{vendor} returned status {response.status_code}",
            provider_status_code=response.status_code,
            provider_error=output if not isinstance(output, str) else {"message": output[:1000]},
            metadata_json={
                "vendor": vendor,
                "status_code": response.status_code,
                "provider_executed": True,
                "retry_safe": method == "GET",
                "outcome_unknown": response.status_code >= 500,
            },
        )
    return response.status_code, output, response.headers
