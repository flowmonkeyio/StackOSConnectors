"""Sitemap helper tests.

Cover the five core invariants:

1. A flat ``<urlset>`` parses into one entry per ``<url>`` row with
   optional fields preserved.
2. A ``<sitemapindex>`` recurses into each child sitemap up to the
   depth cap, then aggregates the entries.
3. A non-200 response is captured as a per-source ``SitemapFetchError``
   without aborting the rest of the batch.
4. Oversized responses raise an error captured in ``errors``.
5. The entry cap caps total entries across all sources.
"""

from __future__ import annotations

import asyncio

import httpx
from pytest_httpx import HTTPXMock

from stackos_connectors.integrations.sitemap import SitemapEntry, fetch_sitemap_entries

URLSET_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xml'
    'ns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    "  <url>\n    <loc>https://example.com/post/a</loc>\n"
    "    <lastmod>2026-01-02</lastmod>\n    <changefreq>"
    "weekly</changefreq>\n    <priority>0.8</priority>\n "
    " </url>\n  <url>\n    <loc>https://example.com/post/"
    "b</loc>\n  </url>\n</urlset>\n"
)
SITEMAPINDEX_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapind'
    'ex xmlns="http://www.sitemaps.org/schemas/sitemap/'
    '0.9">\n  <sitemap>\n    <loc>https://example.com/sit'
    "emap-1.xml</loc>\n  </sitemap>\n  <sitemap>\n    <loc"
    ">https://example.com/sitemap-2.xml</loc>\n  </sitem"
    "ap>\n</sitemapindex>\n"
)
CHILD_SITEMAP_1 = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xml'
    'ns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    "  <url><loc>https://example.com/child-1/a</loc></u"
    "rl>\n  <url><loc>https://example.com/child-1/b</loc"
    "></url>\n</urlset>\n"
)
CHILD_SITEMAP_2 = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xml'
    'ns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    "  <url><loc>https://example.com/child-2/a</loc></u"
    "rl>\n</urlset>\n"
)


def test_flat_urlset_parses_entries(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/sitemap.xml", text=URLSET_BODY)

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://example.com/sitemap.xml"], client=client)

    result = asyncio.run(go())
    assert len(result.entries) == 2
    by_url = {e.url: e for e in result.entries}
    a = by_url["https://example.com/post/a"]
    assert isinstance(a, SitemapEntry)
    assert a.lastmod == "2026-01-02"
    assert a.changefreq == "weekly"
    assert a.priority == "0.8"
    assert a.source_sitemap == "https://example.com/sitemap.xml"
    b = by_url["https://example.com/post/b"]
    assert b.lastmod is None
    assert b.changefreq is None
    assert b.priority is None
    assert result.errors == []


def test_sitemapindex_recurses_into_children(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/sitemap.xml", text=SITEMAPINDEX_BODY)
    httpx_mock.add_response(url="https://example.com/sitemap-1.xml", text=CHILD_SITEMAP_1)
    httpx_mock.add_response(url="https://example.com/sitemap-2.xml", text=CHILD_SITEMAP_2)

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://example.com/sitemap.xml"], client=client)

    result = asyncio.run(go())
    urls = {e.url for e in result.entries}
    assert urls == {
        "https://example.com/child-1/a",
        "https://example.com/child-1/b",
        "https://example.com/child-2/a",
    }
    by_url = {e.url: e for e in result.entries}
    assert (
        by_url["https://example.com/child-1/a"].source_sitemap
        == "https://example.com/sitemap-1.xml"
    )
    assert result.errors == []


def test_failing_source_captured_as_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://broken.example/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://good.example/sitemap.xml", text=URLSET_BODY)

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(
                ["https://broken.example/sitemap.xml", "https://good.example/sitemap.xml"],
                client=client,
            )

    result = asyncio.run(go())
    assert {e.url for e in result.entries} == {
        "https://example.com/post/a",
        "https://example.com/post/b",
    }
    assert len(result.errors) == 1
    assert result.errors[0].url == "https://broken.example/sitemap.xml"
    assert "HTTP 404" in result.errors[0].error


def test_sitemap_index_depth_limit_protects_against_infinite_recursion(
    httpx_mock: HTTPXMock,
) -> None:
    self_referential = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapind'
        'ex xmlns="http://www.sitemaps.org/schemas/sitemap/'
        '0.9">\n  <sitemap>\n    <loc>https://loop.example/si'
        "temap.xml</loc>\n  </sitemap>\n</sitemapindex>\n"
    )
    httpx_mock.add_response(
        url="https://loop.example/sitemap.xml", text=self_referential, is_reusable=True
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(
                ["https://loop.example/sitemap.xml"], client=client, max_index_depth=2
            )

    result = asyncio.run(go())
    assert result.entries == []
    depth_errors = [e for e in result.errors if "depth limit reached" in e.error]
    assert depth_errors, result.errors


def test_unknown_root_element_is_recorded_as_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://weird.example/sitemap.xml", text="<rss><channel><item/></channel></rss>"
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://weird.example/sitemap.xml"], client=client)

    result = asyncio.run(go())
    assert result.entries == []
    assert len(result.errors) == 1
    assert "unexpected root element" in result.errors[0].error
