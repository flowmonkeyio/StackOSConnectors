"""Google Search Console integration wrapper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.redaction import redact_secret_values, redact_secrets
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.google.batch import (
    batch_provider_error,
    batch_retry_after,
    batch_summary,
    encode_batch,
    parse_batch,
)
from stackos_connectors.shared.google.oauth import (
    google_bearer_headers,
    parse_google_oauth_payload,
)


class GoogleSearchConsoleIntegration(BaseIntegration):
    """Wrapper for Search Console reads, sitemap submission and URL Inspection."""

    kind = "google-search-console"
    vendor = "google-search-console"
    default_qps = 2.0

    WEBMASTERS_BASE_URL = "https://www.googleapis.com/webmasters/v3"
    INSPECTION_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
    BATCH_URL = "https://searchconsole.googleapis.com/batch"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._oauth_payload = parse_google_oauth_payload(self.payload, provider=self.vendor)

    def _headers(self) -> dict[str, str]:
        return google_bearer_headers(
            self._oauth_payload,
            provider=self.vendor,
        )

    async def sites_list(self) -> IntegrationCallResult:
        return await self.call(
            op="sites.list",
            method="GET",
            url=f"{self.WEBMASTERS_BASE_URL}/sites",
            headers=self._headers(),
        )

    async def search_analytics_query(
        self,
        *,
        site_url: str,
        request_body: dict[str, Any],
    ) -> IntegrationCallResult:
        encoded_site = quote(site_url, safe="")
        return await self.call(
            op="search_analytics.query",
            method="POST",
            url=f"{self.WEBMASTERS_BASE_URL}/sites/{encoded_site}/searchAnalytics/query",
            json_body=request_body,
            headers=self._headers(),
        )

    async def sitemaps_list(
        self,
        *,
        site_url: str,
        sitemap_index: str | None = None,
    ) -> IntegrationCallResult:
        params = {"sitemapIndex": sitemap_index} if sitemap_index else None
        encoded_site = quote(site_url, safe="")
        return await self.call(
            op="sitemaps.list",
            method="GET",
            url=f"{self.WEBMASTERS_BASE_URL}/sites/{encoded_site}/sitemaps",
            params=params,
            headers=self._headers(),
        )

    async def url_inspect(
        self,
        *,
        site_url: str,
        inspection_url: str,
        language_code: str | None = None,
    ) -> IntegrationCallResult:
        body = {"inspectionUrl": inspection_url, "siteUrl": site_url}
        if language_code:
            body["languageCode"] = language_code
        return await self.call(
            op="url.inspect",
            method="POST",
            url=self.INSPECTION_URL,
            json_body=body,
            headers=self._headers(),
        )

    async def sitemaps_submit(self, *, site_url: str, sitemap_url: str) -> IntegrationCallResult:
        # Google requires an empty request body and returns an empty success body.
        # https://developers.google.com/webmaster-tools/v1/sitemaps/submit
        response = await self.call(
            op="sitemaps.submit",
            method="PUT",
            url=(
                f"{self.WEBMASTERS_BASE_URL}/sites/{quote(site_url, safe='')}/sitemaps/"
                f"{quote(sitemap_url, safe='')}"
            ),
            headers=self._headers(),
            max_retries=0,
        )
        response.data = {"site_url": site_url, "sitemap_url": sitemap_url, "submitted": True}
        return response

    @staticmethod
    def search_analytics_body(payload: dict[str, Any]) -> dict[str, Any]:
        body = {"startDate": payload["start_date"], "endDate": payload["end_date"]}
        for source, target in {
            "dimensions": "dimensions",
            "type": "type",
            "dimension_filter_groups": "dimensionFilterGroups",
            "aggregation_type": "aggregationType",
            "row_limit": "rowLimit",
            "start_row": "startRow",
            "data_state": "dataState",
        }.items():
            if source in payload and payload[source] is not None:
                body[target] = payload[source]
        return body

    async def batch_read(self, *, requests: list[dict[str, Any]]) -> IntegrationCallResult:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []
        for item in requests:
            payload = item["input"]
            operation = item["operation"]
            site = quote(payload.get("site_url", ""), safe="")
            path = f"/webmasters/v3/sites/{site}"
            call: tuple[str, str, dict[str, Any] | None]
            match operation:
                case "sites.list":
                    call = ("GET", "/webmasters/v3/sites", None)
                case "sitemaps.list":
                    query = (
                        "?" + urlencode({"sitemapIndex": payload["sitemap_index"]})
                        if payload.get("sitemap_index")
                        else ""
                    )
                    call = ("GET", path + "/sitemaps" + query, None)
                case "search_analytics.query":
                    call = (
                        "POST",
                        path + "/searchAnalytics/query",
                        self.search_analytics_body(payload),
                    )
                case "url.inspect":
                    body = {
                        "siteUrl": payload["site_url"],
                        "inspectionUrl": payload["inspection_url"],
                    }
                    if payload.get("language_code"):
                        body["languageCode"] = payload["language_code"]
                    call = ("POST", "/v1/urlInspection/index:inspect", body)
                case _:
                    raise ValueError("Unsupported Search Console batch read operation")
            calls.append(call)
        return await self._batch("batch.read", [item["operation"] for item in requests], calls)

    async def sitemaps_submit_batch(
        self, *, requests: list[dict[str, Any]]
    ) -> IntegrationCallResult:
        calls: list[tuple[str, str, dict[str, Any] | None]] = [
            (
                "PUT",
                f"/webmasters/v3/sites/{quote(item['site_url'], safe='')}/sitemaps/"
                f"{quote(item['sitemap_url'], safe='')}",
                None,
            )
            for item in requests
        ]
        return await self._batch(
            "sitemaps.submit.batch", ["sitemaps.submit"] * len(requests), calls
        )

    def _safe_batch_data(self, data: Any) -> Any:
        secrets = tuple(
            value for value in self._oauth_payload.values() if isinstance(value, str) and value
        )
        return redact_secrets(redact_secret_values(data, secrets))

    async def _batch(
        self, op: str, operations: list[str], calls: list[tuple[str, str, dict[str, Any] | None]]
    ) -> IntegrationCallResult:
        if not 1 <= len(calls) <= 1000:
            raise ValueError("Search Console batches require 1 through 1000 requests")
        content, boundary = encode_batch(calls)

        def parse(response: httpx.Response) -> dict[str, Any]:
            secrets = tuple(
                value for value in self._oauth_payload.values() if isinstance(value, str) and value
            )

            def parse_result(index: int, data: Any, empty: bool) -> dict[str, Any]:
                if operations[index] == "sitemaps.submit" and empty:
                    return {"submitted": True}
                if not isinstance(data, dict):
                    raise ValueError("Expected Google response object")
                return data

            summary = self._safe_batch_data(
                parse_batch(response, operations, parse_result, secrets)
            )
            for item, (_, _, body) in zip(summary["items"], calls, strict=True):
                if (
                    item["outcome"] == "success"
                    and item["operation"] == "search_analytics.query"
                    and body
                ):
                    rows = item["result"].get("rows")
                    limit = body.get("rowLimit")
                    if isinstance(rows, list) and isinstance(limit, int) and len(rows) == limit:
                        item["result"]["next_start_row"] = body.get("startRow", 0) + limit
            if (
                summary["summary"]["failed"]
                or summary["summary"]["unknown"]
                or summary.get("protocol_error")
            ):
                raise IntegrationDownError(
                    "Google batch response is incomplete or contains errors",
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
                request_log_body={"request_count": len(calls), "operations": operations},
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
                    for index, operation in enumerate(operations)
                ]
                exc.data["provider_error"] = batch_summary(items)
                if retry_after is not None:
                    exc.data["provider_error"]["retry_after"] = retry_after
            raise

    def _submission_failure_data(
        self, status: int | None, raw: Any, retry_after: Any = None
    ) -> dict[str, Any]:
        # Redact actual credential values before bounding safe Google fields.
        # An earlier Base preview has lost structure and may contain partial
        # secrets; the allowlist discards it instead of forwarding any preview.
        error = batch_provider_error(self._safe_batch_data(raw))
        if "error" not in error:
            error = {"message": "Google sitemap submission failed"}
        unknown = status is None or status >= 500
        error.update(
            outcome_unknown=unknown,
            retry_safe=False,
            reconcile_before_retry=unknown,
        )
        data: dict[str, Any] = {"status": status, "provider_error": error}
        advice = batch_retry_after(retry_after)
        if advice is not None:
            data["retry_after"] = advice
            error["retry_after"] = advice
        return data

    async def _request_with_retry(
        self, method: str, url: str, *, op: str, **kwargs: Any
    ) -> httpx.Response:
        batch = op in {"batch.read", "sitemaps.submit.batch"}
        try:
            response = await super()._request_with_retry(method, url, op=op, **kwargs)
        except (IntegrationDownError, RateLimitedError) as exc:
            if op == "sitemaps.submit":
                raise type(exc)(
                    "Google sitemap submission failed",
                    data=self._submission_failure_data(
                        exc.data.get("status"),
                        exc.data.get("provider_error"),
                        exc.data.get("retry_after"),
                    ),
                ) from None
            if not batch:
                raise
            # Base raises before response parsing on outer HTTP errors. Replace
            # raw MIME/text here, before Base's failure audit sees it.
            data = {key: value for key, value in exc.data.items() if key == "status"}
            raw = exc.data.get("provider_error")
            data["provider_error"] = batch_provider_error(self._safe_batch_data(raw))
            retry_after = batch_retry_after(exc.data.get("retry_after"))
            if retry_after is not None:
                data["retry_after"] = retry_after
                data["provider_error"]["retry_after"] = retry_after
            raise type(exc)("Google batch transport failed", data=data) from None
        if batch and not 200 <= response.status_code < 300:
            raise IntegrationDownError(
                "Unexpected Google batch HTTP status",
                data={
                    "status": response.status_code,
                    "provider_error": {"message": "Google batch requires HTTP 2xx"},
                },
            )
        if op == "sitemaps.submit" and not 200 <= response.status_code < 300:
            # A redirect is not Google's documented submission success. Raise
            # inside BaseIntegration.call so the failed transport is audited.
            raise IntegrationDownError(
                "Unexpected sitemap submission HTTP status",
                data=self._submission_failure_data(
                    response.status_code,
                    self._provider_error(response),
                    response.headers.get("retry-after"),
                ),
            )
        return response

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.sites_list()
        data = result.data if isinstance(result.data, dict) else {}
        entries_raw = data.get("siteEntry")
        entries = entries_raw if isinstance(entries_raw, list) else []
        permission_levels = sorted(
            {
                level
                for entry in entries
                if isinstance(entry, dict)
                for level in [entry.get("permissionLevel")]
                if isinstance(level, str)
            }
        )
        return {
            "ok": True,
            "vendor": self.vendor,
            "site_count": len(entries),
            "permission_levels": permission_levels,
        }


__all__ = ["GoogleSearchConsoleIntegration"]
