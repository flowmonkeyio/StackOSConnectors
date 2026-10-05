"""Google PAA wrapper tests — delegates to Firecrawl."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos_connectors.connectors.firecrawl.integration import FirecrawlIntegration
from stackos_connectors.connectors.google_paa.integration import GooglePaaIntegration
from stackos_connectors.errors import IntegrationDownError


def test_extract_questions_from_serp_markdown(httpx_mock: HTTPXMock) -> None:
    """``extract`` scrapes Google SERP via Firecrawl and parses PAA questions."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={
            "data": {
                "markdown": (
                    "## People also ask\n- What is content marketing?\n- "
                    "How does SEO work?\n- Why is keyword research impor"
                    "tant?\n"
                )
            }
        },
    )

    async def go() -> dict:
        async with httpx.AsyncClient() as client:
            firecrawl = FirecrawlIntegration(payload=b"fc", http=client)
            paa = GooglePaaIntegration(payload=b"", http=client, firecrawl=firecrawl)
            return await paa.extract(query="content marketing")

    out = asyncio.run(go())
    assert "What is content marketing?" in out["questions"]
    assert len(out["questions"]) >= 3


def test_extract_without_firecrawl_raises() -> None:
    """Constructing without a ``firecrawl`` instance and calling ``extract`` errors."""

    async def go() -> dict:
        async with httpx.AsyncClient() as client:
            paa = GooglePaaIntegration(payload=b"", http=client, firecrawl=None)
            return await paa.extract(query="x")

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())
