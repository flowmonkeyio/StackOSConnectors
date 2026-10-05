"""Explicit single and batch Google Indexing notifications and history."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from urllib.parse import urlparse

import httpx

from stackos_connectors.connectors.google_indexing.integration import GoogleIndexingIntegration
from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import IntegrationDownError, RateLimitedError, ValidationError
from stackos_connectors.shared.google.batch import encode_http_request
from stackos_connectors.shared.provider_utils import connector_error_from_integration
from stackos_connectors.shared.vendor_utils import (
    credential_payload,
    issue,
    required_str,
    result,
    unknown_operation,
)


class GoogleIndexingActionConnector:
    key = "google-indexing"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation in {"batch.publish", "batch.metadata.get"}:
            return self._validate_batch(request)
        if request.operation not in {"url_notifications.publish", "url_notifications.metadata.get"}:
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ValidationIssue] = []
        required_str(payload, "url", issues)
        value = payload.get("url")
        if isinstance(value, str):
            try:
                parsed = urlparse(value)
            except ValueError:
                parsed = None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                issues.append(issue("$.url", "url must be an absolute HTTP or HTTPS URL", "format"))
        allowed = {"url"}
        if request.operation == "url_notifications.publish":
            allowed.add("type")
            if payload.get("type") not in ("URL_UPDATED", "URL_DELETED"):
                issues.append(issue("$.type", "type must be URL_UPDATED or URL_DELETED", "enum"))
        if set(payload) - allowed:
            issues.append(issue("$", "Unsupported Indexing input fields", "additional_properties"))
        return issues

    def _validate_batch(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        publish = request.operation == "batch.publish"
        issues: list[ValidationIssue] = []
        allowed = {"urls", "type"} if publish else {"urls"}
        if set(payload) - allowed:
            issues.append(issue("$", "Unsupported Indexing batch fields", "additional_properties"))
        notification_type = payload.get("type") if publish else None
        if publish and notification_type not in ("URL_UPDATED", "URL_DELETED"):
            issues.append(issue("$.type", "type must be URL_UPDATED or URL_DELETED", "enum"))
        urls = payload.get("urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
            return [*issues, issue("$.urls", "urls must contain 1 through 100 URLs", "range")]
        for index, url in enumerate(urls):
            child = replace(
                request, operation="url_notifications.metadata.get", input_json={"url": url}
            )
            child_issues = self.validate(child)
            issues.extend(
                issue(f"$.urls[{index}]", entry.message, entry.code) for entry in child_issues
            )
            if child_issues or (
                publish and notification_type not in ("URL_UPDATED", "URL_DELETED")
            ):
                continue
            call = GoogleIndexingIntegration.batch_call(url, notification_type)
            if len(encode_http_request(*call)) > GoogleIndexingIntegration.MAX_BATCH_REQUEST_BYTES:
                issues.append(issue(f"$.urls[{index}]", "Encoded request exceeds 1 MB", "size"))
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        payload = request.input_json
        try:
            async with (
                nullcontext(request.options.http)
                if request.options.http is not None
                else httpx.AsyncClient(
                    timeout=(
                        request.options.timeout if request.options.timeout is not None else 60.0
                    )
                )
            ) as http:
                client = GoogleIndexingIntegration(
                    payload=credential_payload(request),
                    rate_limiter=request.options.rate_limiter,
                    http=http,
                )
                match request.operation:
                    case "batch.publish":
                        response = await client.batch_publish(
                            urls=payload["urls"], notification_type=payload["type"]
                        )
                    case "batch.metadata.get":
                        response = await client.batch_metadata(urls=payload["urls"])
                    case "url_notifications.publish":
                        response = await client.publish(
                            url=payload["url"], notification_type=payload["type"]
                        )
                    case "url_notifications.metadata.get":
                        response = await client.get_metadata(url=payload["url"])
                    case _:
                        raise ValidationError("Unsupported Google Indexing operation")
        except (IntegrationDownError, RateLimitedError) as exc:
            error = connector_error_from_integration(
                exc, provider=self.key, operation=request.operation
            )
            provider_error = error.provider_error
            assert isinstance(provider_error, dict)
            if request.operation in {"batch.publish", "batch.metadata.get"}:
                error.output_json.update(provider_error)
            # The operation envelope forwards provider_error, while ActionCall
            # stores output_json. Keep unknown/retry guidance in both owners.
            if request.operation == "url_notifications.publish":
                error.output_json.update(
                    {
                        key: provider_error[key]
                        for key in (
                            "outcome_unknown",
                            "retry_safe",
                            "reconcile_before_retry",
                        )
                    }
                )
            raise error from exc
        return result(self.key, request.operation, response.data, response.cost_usd)


__all__ = ["GoogleIndexingActionConnector"]
