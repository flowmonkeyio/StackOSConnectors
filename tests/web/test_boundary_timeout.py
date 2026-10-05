import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth
from stackos_connectors.shared.base import BaseIntegration

from .catalog_fixture import client_for


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 99), (1.25, 1.25)])
async def test_supplied_client_timeout_on_retries(monkeypatch, override, expected):
    requests = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("stackos_connectors.shared.base.asyncio.sleep", no_sleep)

    def handle(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == expected
        if len(requests) == 1:
            return httpx.Response(503, text="retry")
        return httpx.Response(200, json={"organic": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=99) as http:
        await client_for("serper").execute(
            "serper",
            "serper.search",
            {"query": "test"},
            ConnectorAuth("api_key", {"api_key": "synthetic"}),
            CallOptions(http=http, timeout=override),
        )
        assert not http.is_closed
        assert http.timeout.read == 99
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 99), (1.25, 1.25)])
async def test_supplied_sitemap_stream_timeout(monkeypatch, override, expected):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == expected
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/index.xml"})
        if request.url.path == "/index.xml":
            return httpx.Response(
                200,
                text="<sitemapindex><sitemap><loc>https://example.test/child.xml</loc></sitemap></sitemapindex>",
            )
        return httpx.Response(
            200, text="<urlset><url><loc>https://example.test/page</loc></url></urlset>"
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), timeout=99, follow_redirects=True
    ) as http:
        result = await client_for("sitemap").execute(
            "sitemap",
            "sitemap.fetch",
            {"urls": ["https://example.test/redirect"]},
            options=CallOptions(http=http, timeout=override),
        )
        assert not http.is_closed
        assert http.timeout.read == 99
    assert result.output_json["errors"] == []
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_base_stream_consumer_uses_request_override():
    from stackos_connectors.shared.media import download_generated_media

    def handle(request):
        assert request.extensions["timeout"]["read"] == 2.5
        return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=99) as http:
        integration = BaseIntegration(payload=b"", http=http, timeout=2.5)
        raw, ext = await download_generated_media(
            integration, "https://example.test/image", fallback_ext="bin"
        )
        assert raw == b"image" and ext == "png"
        assert not http.is_closed and http.timeout.read == 99
