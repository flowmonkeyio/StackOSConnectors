import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth

from .catalog_fixture import client_for


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 30), (60, 60), (7.5, 7.5)])
async def test_serper_default_deadline_and_exact_override(monkeypatch, override, expected):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == expected
        return httpx.Response(200, json={"organic": []})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    options = CallOptions() if override is None else CallOptions(timeout=override)
    await client_for("serper").execute(
        "serper",
        "serper.search",
        {"query": "timeout"},
        ConnectorAuth("api_key", {"api_key": "synthetic-timeout"}),
        options,
    )
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 12), (7, 7)])
async def test_sitemap_follows_redirect_with_native_input_deadline(monkeypatch, override, expected):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == expected
        if request.url.path == "/redirect.xml":
            return httpx.Response(302, headers={"location": "/actual.xml"})
        return httpx.Response(
            200, text="<urlset><url><loc>https://example.test/page</loc></url></urlset>"
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    options = CallOptions() if override is None else CallOptions(timeout=override)
    result = await client_for("sitemap").execute(
        "sitemap",
        "sitemap.fetch",
        {"urls": ["https://example.test/redirect.xml"], "timeout_s": 12},
        options=options,
    )
    assert result.output_json["errors"] == []
    assert result.output_json["entries"][0]["url"] == "https://example.test/page"
    assert len(requests) == 2
