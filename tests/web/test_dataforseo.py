"""DataForSEO wrapper — happy path + retry on 429 + budget pre-emption + cost reconciliation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos_connectors.connectors.dataforseo.integration import DataForSeoIntegration
from stackos_connectors.errors import RateLimitedError


def _make(*, http: httpx.AsyncClient) -> DataForSeoIntegration:
    return DataForSeoIntegration(login="u", payload=b"p", http=http)


def _json_body(request: httpx.Request) -> Any:
    return json.loads(request.content.decode("utf-8"))


def test_serp_call_returns_parsed_response(httpx_mock: HTTPXMock) -> None:
    """Happy path: a 200 with the DataForSEO ``tasks`` envelope decodes."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={
            "tasks": [
                {"cost": 0.0025, "result": [{"items": [{"keyword": "x", "rank_absolute": 1}]}]}
            ]
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = _make(http=client)
            return await integ.serp(keyword="x")

    result = asyncio.run(go())
    assert result.data["tasks"][0]["cost"] == 0.0025
    assert pytest.approx(result.cost_usd, abs=1e-06) == 0.0025
    assert _json_body(httpx_mock.get_requests()[0]) == [
        {"keyword": "x", "location_name": "United States", "language_code": "en", "depth": 100}
    ]


def test_retry_on_429_then_succeed(httpx_mock: HTTPXMock) -> None:
    """A single 429 with no Retry-After header retries with backoff."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        status_code=429,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={"tasks": [{"cost": 0.001, "result": []}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = DataForSeoIntegration(login="u", payload=b"p", http=client)
            return await integ.serp(keyword="x")

    result = asyncio.run(go())
    assert len(httpx_mock.get_requests()) == 2
    assert result.data["tasks"][0]["cost"] == 0.001


def test_keyword_volume_posts_task_array(httpx_mock: HTTPXMock) -> None:
    """DataForSEO v3 live endpoints take the task array as the top-level body."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live",
        json={"tasks": [{"cost": 0.001, "result": []}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = _make(http=client)
            return await integ.keyword_volume(keywords=["buy laptop"])

    asyncio.run(go())
    assert _json_body(httpx_mock.get_requests()[0]) == [
        {"keywords": ["buy laptop"], "location_name": "United States", "language_code": "en"}
    ]


def test_intersection_posts_two_targets(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/dataforseo_labs/google/domain_intersection/live",
        json={"tasks": [{"cost": 0.001, "result": []}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = _make(http=client)
            return await integ.intersection(domains=["cnn.com", "forbes.com"])

    asyncio.run(go())
    assert _json_body(httpx_mock.get_requests()[0])[0]["target1"] == "cnn.com"
    assert _json_body(httpx_mock.get_requests()[0])[0]["target2"] == "forbes.com"


def test_intersection_requires_exactly_two_domains() -> None:

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = _make(http=client)
            return await integ.intersection(domains=["one.example"])

    with pytest.raises(ValueError, match="exactly two"):
        asyncio.run(go())


def test_paa_uses_organic_serp_click_depth(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={"tasks": [{"cost": 0.001, "result": []}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = _make(http=client)
            return await integ.paa(keyword="coffee")

    asyncio.run(go())
    body = _json_body(httpx_mock.get_requests()[0])
    assert body[0]["keyword"] == "coffee"
    assert body[0]["people_also_ask_click_depth"] == 1


def test_429_after_max_retries_raises_rate_limited(httpx_mock: HTTPXMock) -> None:
    """Repeated 429s exhaust retries and surface ``RateLimitedError``."""
    for _ in range(4):
        httpx_mock.add_response(
            method="POST",
            url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
            status_code=429,
        )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = DataForSeoIntegration(login="u", payload=b"p", http=client)
            return await integ.serp(keyword="x")

    with pytest.raises(RateLimitedError):
        asyncio.run(go())
