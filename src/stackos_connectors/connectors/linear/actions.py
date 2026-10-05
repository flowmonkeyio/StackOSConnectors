"""Named native provider actions with resolved authentication and plain data."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

import httpx

from stackos_connectors import ConnectorError, ConnectorRequest, ConnectorResult
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.redaction import redact_secrets

from .contract import LINEAR_ACTION_SPECS, LinearActionSpec
from .integration import LinearIntegration


class LinearActionConnector:
    key = "linear"

    def validate(self, request):
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        spec = LINEAR_ACTION_SPECS[request.action_key]
        assert request.auth is not None
        payload = (
            request.auth.fields["api_key"].encode()
            if request.auth.method == "personal_api_key"
            else json.dumps(thaw(request.auth.fields)).encode()
        )
        async with AsyncExitStack() as stack:
            http = request.options.http
            if http is None:
                http = await stack.enter_async_context(httpx.AsyncClient(timeout=60.0))
            integration = LinearIntegration(
                payload=payload,
                http=http,
                auth_method_key=request.auth.method,
                timeout=request.options.timeout,
                rate_limiter=request.options.rate_limiter,
            )
            try:
                result = await integration.execute_document(
                    document_path=spec.document,
                    variables=dict(request.input_json) or None,
                    op=f"action.{request.action_key}",
                    write=spec.write,
                )
            except (IntegrationDownError, RateLimitedError) as exc:
                raise _connector_error(exc, request=request, spec=spec) from None
        return ConnectorResult(
            output_json={"body": result.data},
            metadata_json={
                "vendor": "linear",
                "operation": request.action_key,
                "schema_ref": spec.document,
                "schema_operation": spec.root,
                **(result.metadata or {}),
            },
        )


def _connector_error(
    exc: IntegrationDownError | RateLimitedError,
    *,
    request: ConnectorRequest,
    spec: LinearActionSpec,
) -> ConnectorError:
    data = dict(exc.data) if isinstance(exc.data, dict) else {}
    status = data.get("status")
    provider_status_code = status if isinstance(status, int) else None
    provider_error: dict[str, Any] = {
        "reason_code": str(data.get("reason_code") or "provider_failure"),
        "outcome_unknown": bool(data.get("outcome_unknown")),
    }
    for key in ("partial_data", "rate_limit", "retry_after"):
        if key in data:
            provider_error[key] = data[key]
    if isinstance(data.get("provider_error"), dict):
        provider_error["details"] = data["provider_error"]
    safe_provider_error = redact_secrets(provider_error)
    return ConnectorError(
        "Linear action failed",
        provider_status_code=provider_status_code,
        provider_error=safe_provider_error,
        output_json={
            "status": "failed",
            "provider_status_code": provider_status_code,
            "provider_error": safe_provider_error,
        },
        metadata_json={
            "vendor": "linear",
            "operation": request.action_key,
            "schema_ref": spec.document,
            "schema_operation": spec.root,
        },
    )
