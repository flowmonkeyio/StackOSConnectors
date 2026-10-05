"""Ahrefs wrapper tests — optional API key with graceful degrade."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos_connectors.actions.ahrefs import AhrefsActionConnector, row_limit_for_subscription
from stackos_connectors.contracts import ConnectorAuth, ConnectorRequest
from stackos_connectors.errors import IntegrationDownError, ValidationError
from stackos_connectors.integrations.ahrefs import AhrefsIntegration


def test_keywords_for_site_with_key(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.ahrefs.com/v3/site-explorer/organic-ke"
            "ywords?target=example.com&country=us&limit=100&dat"
            "e=2025-01-01&select=keyword%2Cvolume%2Ccpc%2Cbest_"
            "position%2Ckeyword_difficulty"
        ),
        json={"keywords": [{"keyword": "x", "volume": 1000}]},
        headers={
            "x-api-rows": "1",
            "x-api-units-cost-row": "21",
            "x-api-units-cost-total": "50",
            "x-api-units-cost-total-actual": "50",
            "x-api-cache": "MISS",
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"ah-key", http=client)
            return await integ.keywords_for_site(target="example.com", date_="2025-01-01")

    result = asyncio.run(go())
    assert result.data["keywords"][0]["keyword"] == "x"
    assert result.metadata == {
        "api_units": {
            "rows": 1,
            "cost_row": 21,
            "cost_total": 50,
            "cost_total_actual": 50,
            "cache": "MISS",
        }
    }


def test_limits_and_usage_uses_free_subscription_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.ahrefs.com/v3/subscription-info/limits-and-usage",
        json={
            "limits_and_usage": {
                "subscription": "Standard",
                "units_limit_api_key": 400000,
                "units_usage_api_key": 1200,
                "usage_reset_date": "2026-07-01",
            }
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"ah-key", http=client)
            return await integ.limits_and_usage()

    result = asyncio.run(go())
    assert result.data["limits_and_usage"]["subscription"] == "Standard"
    assert result.metadata is None


def test_top_backlinks_uses_v3_all_backlinks_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.ahrefs.com/v3/site-explorer/all-backli"
            "nks?target=example.com&mode=domain&limit=100&selec"
            "t=url_from%2Curl_to%2Cdomain_rating_source%2Cfirst"
            "_seen"
        ),
        json={"backlinks": [{"url_from": "https://source.example/a"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"ah-key", http=client)
            return await integ.top_backlinks(target="example.com")

    result = asyncio.run(go())
    assert result.data["backlinks"][0]["url_from"] == "https://source.example/a"


def test_test_credentials_without_key_raises_with_hint() -> None:
    """Empty payload → graceful optional-integration error pointing at docs."""

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"", http=client)
            return await integ.test_credentials()

    with pytest.raises(IntegrationDownError) as exc_info:
        asyncio.run(go())
    assert "Ahrefs API key is not configured" in exc_info.value.detail
    assert "docs/api-keys.md" in exc_info.value.data["hint"]


def _action_request(operation, input_json):
    return ConnectorRequest(
        connector="ahrefs",
        action_key=operation,
        operation=operation,
        input_json=input_json,
        config_json={},
        auth=ConnectorAuth("api_key", {"api_key": "ah-key"}),
    )


def test_row_limit_for_subscription_matches_ahrefs_pricing_docs() -> None:
    assert row_limit_for_subscription("Lite") == (100, "documented_plan_cap")
    assert row_limit_for_subscription("Standard") == (250, "documented_plan_cap")
    assert row_limit_for_subscription("Advanced") == (500, "documented_plan_cap")
    assert row_limit_for_subscription("Enterprise") == (None, "enterprise_unlimited")
    assert row_limit_for_subscription("Starter") == (0, "no_direct_api")
    assert row_limit_for_subscription(None) == (100, "unknown_subscription_safe_default")


def test_action_connector_validates_documented_mode_and_action_cap() -> None:
    connector = AhrefsActionConnector()
    issues = connector.validate(
        _action_request(
            "backlink.research", {"target": "example.com", "mode": "bad-mode", "limit": 1001}
        )
    )
    assert {issue.code for issue in issues} == {"enum_mismatch", "range"}


def test_action_connector_checks_plan_before_paid_keyword_call(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.ahrefs.com/v3/subscription-info/limits-and-usage",
        json={"limits_and_usage": {"subscription": "Lite"}},
    )

    async def go() -> None:
        await AhrefsActionConnector().execute(
            _action_request(
                "competitor.keywords", {"target": "example.com", "limit": 101, "date": "2026-06-15"}
            )
        )

    with pytest.raises(ValidationError) as exc_info:
        asyncio.run(go())
    assert exc_info.value.data["effective_row_limit"] == 100
    assert len(httpx_mock.get_requests()) == 1


def test_action_connector_rejects_unknown_operation_before_provider_call() -> None:

    async def go() -> None:
        await AhrefsActionConnector().execute(
            _action_request("unknown.operation", {"target": "example.com"})
        )

    with pytest.raises(ValidationError, match="unsupported Ahrefs operation"):
        asyncio.run(go())


def test_action_connector_surfaces_subscription_and_unit_metadata(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.ahrefs.com/v3/subscription-info/limits-and-usage",
        json={
            "limits_and_usage": {
                "subscription": "Standard",
                "units_limit_api_key": 400000,
                "units_usage_api_key": 1234,
                "usage_reset_date": "2026-07-01",
            }
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.ahrefs.com/v3/site-explorer/organic-ke"
            "ywords?target=example.com&country=us&limit=250&dat"
            "e=2026-06-15&select=keyword%2Cvolume%2Ccpc%2Cbest_"
            "position%2Ckeyword_difficulty"
        ),
        json={"keywords": [{"keyword": "crm seo"}]},
        headers={
            "x-api-rows": "1",
            "x-api-units-cost-row": "21",
            "x-api-units-cost-total": "50",
            "x-api-units-cost-total-actual": "50",
        },
    )

    async def go() -> Any:
        return await AhrefsActionConnector().execute(
            _action_request(
                "competitor.keywords", {"target": "example.com", "limit": 250, "date": "2026-06-15"}
            )
        )

    result = asyncio.run(go())
    assert result.output_json["keywords"][0]["keyword"] == "crm seo"
    assert result.metadata_json is not None
    metadata = result.metadata_json["ahrefs"]
    assert metadata["subscription"] == "Standard"
    assert metadata["row_limit"] == 250
    assert metadata["effective_row_limit"] == 250
    assert metadata["units_usage_api_key"] == 1234
    assert metadata["api_units"]["cost_total_actual"] == 50
