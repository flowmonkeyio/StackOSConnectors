"""Google Search Console wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.integrations._base import MAX_LOG_BYTES
from stackos_connectors.integrations.google_search_console import GoogleSearchConsoleIntegration


def _payload() -> bytes:
    return json.dumps({"access_token": "gsc-token"}).encode("utf-8")


def test_search_console_wrapper_maps_read_endpoints(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        json={"siteEntry": [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://www.googleapis.com/webmasters/v3/sites/htt"
            "ps%3A%2F%2Fexample.com%2F/searchAnalytics/query"
        ),
        json={"rows": [{"keys": ["stackos"], "clicks": 1}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://www.googleapis.com/webmasters/v3/sites/htt"
            "ps%3A%2F%2Fexample.com%2F/sitemaps?sitemapIndex=ht"
            "tps%3A%2F%2Fexample.com%2Fsitemap.xml"
        ),
        json={"sitemap": [{"path": "https://example.com/sitemap.xml"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
        json={"inspectionResult": {"inspectionResultLink": "https://search.google.com/test"}},
    )

    async def go() -> list[Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleSearchConsoleIntegration(
                payload=_payload(), http=client, qps_override=1000.0
            )
            sites = await integration.sites_list()
            analytics = await integration.search_analytics_query(
                site_url="https://example.com/",
                request_body={"startDate": "2026-06-01", "endDate": "2026-06-07"},
            )
            sitemaps = await integration.sitemaps_list(
                site_url="https://example.com/", sitemap_index="https://example.com/sitemap.xml"
            )
            inspection = await integration.url_inspect(
                site_url="https://example.com/",
                inspection_url="https://example.com/page",
                language_code="en-US",
            )
            return [sites, analytics, sitemaps, inspection]

    sites, analytics, sitemaps, inspection = asyncio.run(go())
    requests = httpx_mock.get_requests()
    inspection_body = json.loads(requests[3].content.decode("utf-8"))
    assert sites.data["siteEntry"][0]["permissionLevel"] == "siteOwner"
    assert analytics.data["rows"][0]["keys"] == ["stackos"]
    assert sitemaps.data["sitemap"][0]["path"] == "https://example.com/sitemap.xml"
    assert inspection.data["inspectionResult"]["inspectionResultLink"].startswith("https://")
    assert requests[0].headers["Authorization"] == "Bearer gsc-token"
    assert inspection_body == {
        "inspectionUrl": "https://example.com/page",
        "siteUrl": "https://example.com/",
        "languageCode": "en-US",
    }


def test_search_console_test_credentials_returns_sanitized_site_summary(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        json={
            "siteEntry": [
                {"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"},
                {"siteUrl": "sc-domain:example.org", "permissionLevel": "siteRestrictedUser"},
            ]
        },
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleSearchConsoleIntegration(
                payload=_payload(), http=client, qps_override=1000.0
            )
            return await integration.test_credentials()

    assert asyncio.run(go()) == {
        "ok": True,
        "vendor": "google-search-console",
        "site_count": 2,
        "permission_levels": ["siteOwner", "siteRestrictedUser"],
    }


@pytest.mark.parametrize("site", ["sc-domain:example.com", "https://example.com/path/"])
def test_sitemap_submission_encodes_both_paths_and_sends_no_body(httpx_mock, site):
    from urllib.parse import quote

    sitemap = "https://example.com/sitemap.xml?part=1&language=en"
    httpx_mock.add_response(
        method="PUT",
        url=(
            f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/"
            f"sitemaps/{quote(sitemap, safe='')}"
        ),
        status_code=204,
    )

    async def go():
        async with httpx.AsyncClient() as http:
            return await GoogleSearchConsoleIntegration(
                payload=_payload(), http=http
            ).sitemaps_submit(site_url=site, sitemap_url=sitemap)

    assert asyncio.run(go()).data == {"site_url": site, "sitemap_url": sitemap, "submitted": True}
    request = httpx_mock.get_request()
    assert request.content == b""
    assert request.headers["authorization"] == "Bearer gsc-token"


@pytest.mark.parametrize("status", [302, 403, 429, 503, None])
def test_sitemap_submission_never_retries_provider_or_transport_failure(httpx_mock, status):
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic timeout"), method="PUT")
    else:
        httpx_mock.add_response(method="PUT", status_code=status, json={"error": {"code": status}})

    async def go():
        async with httpx.AsyncClient() as http:
            return await GoogleSearchConsoleIntegration(
                payload=_payload(), http=http
            ).sitemaps_submit(
                site_url="sc-domain:example.com", sitemap_url="https://example.com/sitemap.xml"
            )

    with pytest.raises((IntegrationDownError, RateLimitedError)):
        asyncio.run(go())
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("status", [302, 403, 429, 503])
@pytest.mark.parametrize("body_kind", ["json", "oversized", "non_json"])
def test_sitemap_submission_redacts_before_low_level_failure_audit(httpx_mock, status, body_kind):
    token = "GSC-SECRET-CROSSING-PREVIEW-" + "q" * 70
    secret_field = "GSC-SECRET-FIELD-CANARY"
    error = {"error": {"code": status, "access_token": secret_field, "message": ""}}
    if body_kind == "oversized":
        prefix_size = repr(error).index("'message': '") + len("'message': '")
        padding = "x" * (MAX_LOG_BYTES - prefix_size - len(token) // 2)
        error["error"]["message"] = padding + token + "ending"
    else:
        error["error"]["message"] = "Provider echoed " + token
    body = (
        json.dumps(error).encode()
        if body_kind != "non_json"
        else f"Echo {token} {secret_field}".encode()
    )
    httpx_mock.add_response(
        method="PUT", status_code=status, content=body, headers={"Retry-After": "17"}
    )

    async def go():
        async with httpx.AsyncClient() as http:
            return await GoogleSearchConsoleIntegration(
                payload=json.dumps({"access_token": token}).encode(), http=http
            ).sitemaps_submit(
                site_url="sc-domain:example.com", sitemap_url="https://example.com/sitemap.xml"
            )

    with pytest.raises((IntegrationDownError, RateLimitedError)) as caught:
        asyncio.run(go())
    assert caught.value.data["status"] == status
    rendered = json.dumps(
        {"exception": str(caught.value), "data": caught.value.data, "audit": caught.value.data}
    )
    for forbidden in (token, "GSC-SECRET-CROSSING-PREVIEW-", secret_field, "_truncated", "preview"):
        assert forbidden not in rendered
    provider_error = caught.value.data["provider_error"]
    assert provider_error["outcome_unknown"] is (status >= 500)
    assert provider_error["retry_safe"] is False
    assert provider_error["reconcile_before_retry"] is (status >= 500)
    if body_kind == "json":
        assert provider_error["error"]["code"] == status
        assert "Provider echoed" in provider_error["error"]["message"]
    if status == 429:
        assert caught.value.data["retry_after"] == provider_error["retry_after"] == 17
    assert len(httpx_mock.get_requests()) == 1
