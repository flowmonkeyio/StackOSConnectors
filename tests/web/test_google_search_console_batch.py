"""Google batch correlation and safe pre-audit parsing."""

import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from stackos_connectors.connectors.google_search_console.integration import (
    GoogleSearchConsoleIntegration,
)
from stackos_connectors.errors import IntegrationDownError, RateLimitedError

BATCH_URL = "https://searchconsole.googleapis.com/batch"
TOKEN = "synthetic-batch-token"


def multipart(parts):
    chunks = []
    for index, status, body in parts:
        encoded = body if isinstance(body, str) else json.dumps(body)
        chunks.append(
            f"--batch_reply\r\nContent-Type: application/http\r\n"
            f"Content-ID: <response-item-{index}>\r\n\r\nHTTP/1.1 {status} Test\r\n"
            f"Content-Type: application/json\r\nX-Request-Id: part-{index}\r\n\r\n{encoded}\r\n"
        )
    return ("".join(chunks) + "--batch_reply--\r\n").encode()


def run_batch(requests, *, write=False, audit=None):

    async def run():
        async with httpx.AsyncClient() as http:
            wrapper = GoogleSearchConsoleIntegration(
                payload=json.dumps({"access_token": TOKEN}).encode(), http=http
            )
            method = wrapper.sitemaps_submit_batch if write else wrapper.batch_read
            return await method(requests=requests)

    return asyncio.run(run())


def respond(httpx_mock, parts):
    httpx_mock.add_response(
        method="POST",
        url=BATCH_URL,
        content=multipart(parts),
        headers={"Content-Type": 'multipart/mixed; boundary="batch_reply"'},
    )


def test_batch_partial_and_reordered_results_are_safe_before_audit(httpx_mock):
    audit = Mock()
    respond(
        httpx_mock,
        [(1, 403, {"error": {"code": 403, "message": TOKEN}}), (0, 200, {"siteEntry": []})],
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}] * 2, audit=audit)
    summary = caught.value.data["provider_error"]
    assert summary["summary"] == {"total": 2, "succeeded": 1, "failed": 1, "unknown": 0}
    assert summary["partial_success"] is True
    assert [item["index"] for item in summary["items"]] == [0, 1]
    assert [item["outcome"] for item in summary["items"]] == ["success", "error"]
    assert summary["retry_safe"] is False
    recorded = caught.value.data
    assert caught.value.detail
    serialized = json.dumps(recorded)
    for forbidden in (TOKEN, "batch_reply", "HTTP/1.1", "Content-ID"):
        assert forbidden not in serialized
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("status", [403, 429, 503, None])
def test_batch_outer_failure_never_audits_raw_mime_or_retries(httpx_mock, status):
    audit = Mock()
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic transport failure"), url=BATCH_URL)
    else:
        httpx_mock.add_response(
            url=BATCH_URL,
            status_code=status,
            content=b"RAW-MIME-SECRET Content-ID: secret",
            headers={"Content-Type": "multipart/mixed; boundary=secret", "Retry-After": "9"},
        )
    with pytest.raises((IntegrationDownError, RateLimitedError)) as caught:
        run_batch([{"operation": "sites.list", "input": {}}], audit=audit)
    summary = caught.value.data["provider_error"]
    assert summary["summary"]["total"] == 1
    assert summary["items"][0]["outcome"] == (
        "unknown" if status is None or status >= 500 else "error"
    )
    assert "RAW-MIME-SECRET" not in json.dumps(caught.value.data)
    assert "Content-ID" not in json.dumps(caught.value.data)
    assert len(httpx_mock.get_requests()) == 1


def test_batch_maps_all_four_reads_with_outer_auth_and_encoded_paths(httpx_mock):
    respond(httpx_mock, [(i, 200, {"item": i}) for i in reversed(range(4))])
    requests = [
        {"operation": "sites.list", "input": {}},
        {
            "operation": "sitemaps.list",
            "input": {
                "site_url": "sc-domain:example.com",
                "sitemap_index": "https://example.com/map.xml?a=1",
            },
        },
        {
            "operation": "search_analytics.query",
            "input": {
                "site_url": "https://example.com/",
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "row_limit": 10,
            },
        },
        {
            "operation": "url.inspect",
            "input": {
                "site_url": "sc-domain:example.com",
                "inspection_url": "https://example.com/a",
                "language_code": "en-US",
            },
        },
    ]
    output = run_batch(requests).data
    assert [item["result"]["item"] for item in output["items"]] == list(range(4))
    wire = httpx_mock.get_requests()[0]
    assert wire.headers["authorization"] == f"Bearer {TOKEN}"
    body = wire.content.decode()
    assert TOKEN not in body and "Authorization" not in body
    assert "GET /webmasters/v3/sites HTTP/1.1" in body
    assert (
        "GET /webmasters/v3/sites/sc-domain%3Aexample.com/s"
        "itemaps?sitemapIndex=https%3A%2F%2Fexample.com%2Fm"
        "ap.xml%3Fa%3D1 HTTP/1.1"
    ) in body
    assert (
        "POST /webmasters/v3/sites/https%3A%2F%2Fexample.com%2F/searchAnalytics/query HTTP/1.1"
        in body
    )
    assert '"rowLimit": 10' in body
    assert "POST /v1/urlInspection/index:inspect HTTP/1.1" in body
    assert '"inspectionUrl": "https://example.com/a"' in body
    assert '"languageCode": "en-US"' in body
    for index in range(4):
        assert f"Content-ID: <item-{index}>" in body


def test_submit_batch_1000_empty_successes_and_no_nested_bodies(httpx_mock):
    respond(httpx_mock, [(i, 204, "") for i in range(1000)])
    output = run_batch(
        [{"site_url": "sc-domain:example.com", "sitemap_url": "https://example.com/sitemap.xml"}]
        * 1000,
        write=True,
    ).data
    assert output["summary"] == {"total": 1000, "succeeded": 1000, "failed": 0, "unknown": 0}
    assert all(item["result"] == {"submitted": True} for item in output["items"])
    request = httpx_mock.get_requests()[0]
    assert (
        request.content.count(
            b"PUT /webmasters/v3/sites/sc-domain%3Aexample.com/s"
            b"itemaps/https%3A%2F%2Fexample.com%2Fsitemap.xml HT"
            b"TP/1.1\r\n\r\n\r\n"
        )
        == 1000
    )


@pytest.mark.parametrize(
    "parts,unknown",
    [
        ([(1, 200, {"ok": True})], [0]),
        ([(0, 200, {}), (0, 200, {}), (1, 200, {})], [0]),
        ([(0, 200, "invalid-json-secret"), (1, 200, {})], [0]),
        ([(0, 200, {}), (2, 200, {})], [1]),
    ],
)
def test_batch_missing_duplicate_malformed_and_uncorrelated_parts(httpx_mock, parts, unknown):
    respond(httpx_mock, parts)
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}] * 2)
    summary = caught.value.data["provider_error"]
    assert [item["index"] for item in summary["items"] if item["outcome"] == "unknown"] == unknown
    assert "invalid-json-secret" not in json.dumps(summary)


@pytest.mark.parametrize(
    "status,content_type,body",
    [
        (200, "text/html", b"raw-secret-response"),
        (200, "multipart/mixed; boundary=missing", b"raw-secret-response"),
        (302, "text/html", b"raw-secret-response"),
    ],
)
def test_batch_malformed_envelope_and_redirect_never_succeed(
    httpx_mock, status, content_type, body
):
    audit = Mock()
    httpx_mock.add_response(
        url=BATCH_URL, status_code=status, content=body, headers={"Content-Type": content_type}
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}], audit=audit)
    assert "raw-secret-response" not in json.dumps(caught.value.data)
    assert len(httpx_mock.get_requests()) == 1


def test_batch_error_messages_are_redacted_before_bounding(httpx_mock):
    respond(
        httpx_mock, [(0, 403, {"error": {"code": 403, "message": "x" * 490 + TOKEN + "y" * 600}})]
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}])
    message = caught.value.data["provider_error"]["items"][0]["provider_error"]["error"]["message"]
    assert len(message) <= 500
    assert "synthetic" not in message


@pytest.mark.parametrize("defect", ["extra_after", "extra_before", "missing_close"])
def test_batch_protocol_error_preserves_valid_submitted_receipt(httpx_mock, defect):
    parts = [(0, 204, "")]
    if defect == "extra_after":
        parts.append((9, 204, ""))
    elif defect == "extra_before":
        parts.insert(0, (9, 204, ""))
    body = multipart(parts)
    if defect == "missing_close":
        body = body.removesuffix(b"--batch_reply--\r\n")
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    audit = Mock()
    with pytest.raises(IntegrationDownError) as caught:
        run_batch(
            [{"site_url": "sc-domain:example.com", "sitemap_url": "https://example.com/map.xml"}],
            write=True,
            audit=audit,
        )
    summary = caught.value.data["provider_error"]
    assert summary["items"][0]["status"] == 204
    assert summary["items"][0]["outcome"] == "success"
    assert summary["items"][0]["result"] == {"submitted": True}
    assert summary["summary"] == {"total": 1, "succeeded": 1, "failed": 0, "unknown": 0}
    assert summary["protocol_error"]
    assert summary["reconcile_before_retry"] and summary["retry_safe"] is False
    assert caught.value.detail
    assert len(httpx_mock.get_requests()) == 1


def test_batch_inner_429_retains_retry_after_advice_without_retry(httpx_mock):
    body = multipart([(0, 429, {"error": {"code": 429, "message": "Quota reached"}})]).replace(
        b"X-Request-Id: part-0\r\n", b"X-Request-Id: part-0\r\nRetry-After: 17\r\n"
    )
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}])
    item = caught.value.data["provider_error"]["items"][0]
    assert item["status"] == 429
    assert item["retry_after"] == 17
    assert item["provider_error"]["retry_after"] == 17
    assert len(httpx_mock.get_requests()) == 1


def test_batch_outer_429_retains_retry_after_in_returned_summary(httpx_mock):
    httpx_mock.add_response(
        url=BATCH_URL, status_code=429, json={"error": {"code": 429}}, headers={"Retry-After": "17"}
    )
    with pytest.raises(RateLimitedError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}])
    summary = caught.value.data["provider_error"]
    assert summary["retry_after"] == 17
    assert summary["items"][0]["retry_after"] == 17
    assert summary["items"][0]["provider_error"]["retry_after"] == 17
    assert summary["retry_safe"] is False
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("value", ["NaN", "inf", "-1", "invalid", "1" * 65])
def test_batch_invalid_inner_retry_after_is_omitted(httpx_mock, value):
    body = multipart([(0, 429, {"error": {"code": 429}})]).replace(
        b"X-Request-Id: part-0\r\n", f"X-Request-Id: part-0\r\nRetry-After: {value}\r\n".encode()
    )
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_batch([{"operation": "sites.list", "input": {}}])
    item = caught.value.data["provider_error"]["items"][0]
    assert "retry_after" not in item
    assert "retry_after" not in item["provider_error"]
    assert len(httpx_mock.get_requests()) == 1
