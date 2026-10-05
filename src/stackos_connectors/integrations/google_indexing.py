"""Google Indexing notification transport; credentials are resolved by core auth."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.integrations._base import BaseIntegration, IntegrationCallResult
from stackos_connectors.integrations.google_batch import (
    batch_provider_error,
    batch_retry_after,
    batch_summary,
    encode_batch,
    encode_http_request,
    parse_batch,
)
from stackos_connectors.integrations.google_oauth import (
    google_bearer_headers,
    parse_google_oauth_payload,
)
from stackos_connectors.redaction import redact_secret_values, redact_secrets


class GoogleIndexingIntegration(BaseIntegration):
    kind = "google-indexing"
    vendor = "google-indexing"
    default_qps = 2.0
    PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
    METADATA_URL = "https://indexing.googleapis.com/v3/urlNotifications/metadata"
    BATCH_URL = "https://indexing.googleapis.com/batch"
    MAX_BATCH_REQUEST_BYTES = 1_000_000

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._oauth_payload = parse_google_oauth_payload(self.payload, provider=self.vendor)

    def _headers(self) -> dict[str, str]:
        return google_bearer_headers(self._oauth_payload, provider=self.vendor)

    def _safe_data(self, data: Any) -> Any:
        secrets = tuple(
            value for value in self._oauth_payload.values() if isinstance(value, str) and value
        )
        return redact_secrets(redact_secret_values(data, secrets))

    def _failure_data(
        self, op: str, status: int | None, error: Any, *, unknown: bool | None = None
    ) -> dict[str, Any]:
        provider_error = self._safe_data(error)
        if not isinstance(provider_error, dict):
            provider_error = {"message": "Google Indexing request failed"}
        if op == "url_notifications.publish":
            outcome_unknown = (status is None or status >= 500) if unknown is None else unknown
            provider_error.update(
                outcome_unknown=outcome_unknown,
                retry_safe=False,
                reconcile_before_retry=outcome_unknown,
            )
        return {"status": status, "provider_error": provider_error}

    def _parse_metadata(self, response: httpx.Response, *, op: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or "error" in data:
            raise IntegrationDownError(
                "Malformed Google Indexing response",
                data=self._failure_data(
                    op,
                    response.status_code,
                    {"message": "Expected Google notification metadata"},
                    unknown=True,
                ),
            )
        if op == "url_notifications.publish":
            data = data.get("urlNotificationMetadata")
            if not isinstance(data, dict):
                raise IntegrationDownError(
                    "Malformed Google Indexing publish response",
                    data=self._failure_data(
                        op,
                        response.status_code,
                        {"message": "Missing notification metadata"},
                        unknown=True,
                    ),
                )
        return self._safe_data(data)

    async def publish(self, *, url: str, notification_type: str) -> IntegrationCallResult:
        # HTTP acceptance describes a notification, never current indexing state.
        # https://developers.google.com/search/apis/indexing-api/v3/using-api
        op = "url_notifications.publish"
        return await self.call(
            op=op,
            method="POST",
            url=self.PUBLISH_URL,
            json_body={"url": url, "type": notification_type},
            headers=self._headers(),
            max_retries=0,
            response_parser=lambda response: {
                "url": url,
                "type": notification_type,
                "notification_received": True,
                "notification_metadata": self._parse_metadata(response, op=op),
                "indexing_status": "unverified",
            },
        )

    async def get_metadata(self, *, url: str) -> IntegrationCallResult:
        op = "url_notifications.metadata.get"
        return await self.call(
            op=op,
            method="GET",
            url=self.METADATA_URL,
            params={"url": url},
            headers=self._headers(),
            response_parser=lambda response: {
                "url": url,
                "notification_metadata": self._parse_metadata(response, op=op),
                "indexing_status": "unverified",
            },
        )

    @staticmethod
    def batch_call(url: str, notification_type: str | None) -> tuple[str, str, dict | None]:
        if notification_type is not None:
            return "POST", "/v3/urlNotifications:publish", {"url": url, "type": notification_type}
        return "GET", "/v3/urlNotifications/metadata?" + urlencode({"url": url}), None

    async def batch_publish(
        self, *, urls: list[str], notification_type: str
    ) -> IntegrationCallResult:
        return await self._batch(urls=urls, notification_type=notification_type)

    async def batch_metadata(self, *, urls: list[str]) -> IntegrationCallResult:
        return await self._batch(urls=urls, notification_type=None)

    async def _batch(
        self, *, urls: list[str], notification_type: str | None
    ) -> IntegrationCallResult:
        if not 1 <= len(urls) <= 100:
            raise ValueError("Indexing batches require 1 through 100 URLs")
        calls = [self.batch_call(url, notification_type) for url in urls]
        if any(len(encode_http_request(*call)) > self.MAX_BATCH_REQUEST_BYTES for call in calls):
            raise ValueError("Each Indexing batch request must be at most 1 MB")
        publish = notification_type is not None
        op = "batch.publish" if publish else "batch.metadata.get"
        operation = "url_notifications.publish" if publish else "url_notifications.metadata.get"
        operations = [operation] * len(calls)
        content, boundary = encode_batch(calls)

        def parse_result(index: int, data: Any, _empty: bool) -> dict[str, Any]:
            if not isinstance(data, dict) or "error" in data:
                raise ValueError("Expected Google notification metadata")
            if publish:
                data = data.get("urlNotificationMetadata")
                if not isinstance(data, dict):
                    raise ValueError("Missing notification metadata")
            return {
                "url": urls[index],
                **({"type": notification_type, "notification_received": True} if publish else {}),
                "notification_metadata": data,
                "indexing_status": "unverified",
            }

        def parse(response: httpx.Response) -> dict[str, Any]:
            secrets = tuple(
                value for value in self._oauth_payload.values() if isinstance(value, str) and value
            )
            summary = self._safe_data(parse_batch(response, operations, parse_result, secrets))
            if (
                summary["summary"]["failed"]
                or summary["summary"]["unknown"]
                or summary.get("protocol_error")
            ):
                raise IntegrationDownError(
                    "Google Indexing batch is incomplete or contains errors",
                    data={"status": response.status_code, "provider_error": summary},
                )
            return summary

        try:
            return await self.call(
                op=op,
                method="POST",
                url=self.BATCH_URL,
                content=content,
                headers={
                    **self._headers(),
                    "Content-Type": f"multipart/mixed; boundary={boundary}",
                },
                request_log_body={"request_count": len(calls), "operation": operation},
                response_parser=parse,
                max_retries=0,
            )
        except (IntegrationDownError, RateLimitedError) as exc:
            if "items" not in exc.data.get("provider_error", {}):
                status = exc.data.get("status")
                unknown = status is None or status >= 500
                error = exc.data.get("provider_error", {"message": "Google batch transport failed"})
                retry_after = batch_retry_after(exc.data.get("retry_after"))
                items = [
                    {
                        "index": index,
                        "operation": operation,
                        "status": status,
                        "outcome": "unknown" if unknown else "error",
                        "provider_error": error,
                        **({"retry_after": retry_after} if retry_after is not None else {}),
                    }
                    for index in range(len(urls))
                ]
                exc.data["provider_error"] = batch_summary(items)
                if retry_after is not None:
                    exc.data["provider_error"]["retry_after"] = retry_after
            raise

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = {"message": "Google Indexing returned a non-JSON error"}
        # The instance request boundary redacts actual credential values before
        # truncation and before BaseIntegration records failure audit.
        return redact_secrets(body)

    async def _request_with_retry(
        self, method: str, url: str, *, op: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await super()._request_with_retry(method, url, op=op, **kwargs)
        except (IntegrationDownError, RateLimitedError) as exc:
            error = self._safe_data(exc.data.get("provider_error"))
            if op in {"batch.publish", "batch.metadata.get"}:
                error = batch_provider_error(error)
            else:
                error = self._truncate(error)
            data = self._failure_data(op, exc.data.get("status"), error)
            retry_after = batch_retry_after(exc.data.get("retry_after"))
            if retry_after is not None:
                data["retry_after"] = retry_after
                data["provider_error"]["retry_after"] = retry_after
            raise type(exc)("Google Indexing request failed", data=data) from None
        if not 200 <= response.status_code < 300:
            raise IntegrationDownError(
                "Unexpected Google Indexing HTTP status",
                data=self._failure_data(
                    op,
                    response.status_code,
                    {"message": "Google Indexing requires HTTP 2xx"},
                ),
            )
        return response


__all__ = ["GoogleIndexingIntegration"]
