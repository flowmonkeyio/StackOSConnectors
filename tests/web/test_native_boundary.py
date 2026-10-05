import json
from importlib import resources

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ValidationError

from .catalog_fixture import client_for


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,operation",
    [("competitor.keywords", "organic-keywords"), ("backlink.research", "all-backlinks")],
)
async def test_ahrefs_native_paid_route_has_no_host_admission(action, operation):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer synthetic"
        assert request.content == b""
        assert request.url.path == f"/v3/site-explorer/{operation}"
        assert request.url.params["limit"] == "1501"
        return httpx.Response(200, json={"rows": []}, headers={"x-api-rows": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for("ahrefs").execute(
            "ahrefs",
            action,
            {"target": "example.test", "limit": 1501},
            ConnectorAuth("api_key", {"api_key": "synthetic"}),
            CallOptions(http=http),
        )
    assert result.output_json == {"rows": []}
    assert result.metadata_json["api_units"]["rows"] == 0
    assert "ahrefs" not in result.metadata_json
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_ahrefs_limits_are_explicit_native_read():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer synthetic"
        assert request.content == b""
        assert request.url.path == "/v3/subscription-info/limits-and-usage"
        return httpx.Response(200, json={"limits_and_usage": {"subscription": "Free"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for("ahrefs").execute(
            "ahrefs",
            "limits_and_usage",
            {},
            ConnectorAuth("api_key", {"api_key": "synthetic"}),
            CallOptions(http=http),
        )
    assert result.output_json["limits_and_usage"]["subscription"] == "Free"
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,operation,connection",
    [
        ("list_inventory_item_availability", "LowStockReport", "inventoryItems"),
        ("list_product_variant_inventory", "InventoryRiskReport", "productVariants"),
        ("list_order_variant_quantities", "RecentSales", "orders"),
    ],
)
async def test_shopify_fixed_fetch_one_page(action, operation, connection):
    seen = []
    data = {"first": 17, "after": "native-cursor"}
    if connection == "orders":
        data["query"] = "created_at:>=2026-01-01"
    expected = {connection: {"edges": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://demo.myshopify.com/admin/api/2026-07/graphql.json"
        assert request.headers["x-shopify-access-token"] == "synthetic"
        body = json.loads(request.content)
        assert f"query {operation}(" in body["query"]
        assert body["variables"] == data
        return httpx.Response(200, json={"data": expected})

    auth = ConnectorAuth(
        "admin-api-token",
        {"admin_api_access_token": "synthetic"},
        {"store_domain": "demo.myshopify.com"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = client_for("shopify")
        result = await client.execute("shopify", action, data, auth, CallOptions(http=http))
        for invalid in (
            {**data, "first": 251},
            {**data, "first": True},
            {**data, "graphql": "query arbitrary { shop { id } }"},
        ):
            with pytest.raises(ValidationError):
                await client.execute("shopify", action, invalid, auth, CallOptions(http=http))
        if connection == "orders":
            with pytest.raises(ValidationError):
                await client.execute("shopify", action, {"first": 1}, auth, CallOptions(http=http))
    assert result.output_json["data"] == expected
    assert len(seen) == 1


def test_composition_and_audit_helpers_are_not_portable():
    from pydantic import ValidationError as ModelError

    from stackos_connectors.probe import PermissionVerification
    from stackos_connectors.shared import media

    keys = {a["key"] for a in client_for("shopify").describe("shopify")["actions"]}
    assert not {"low_stock_report", "inventory_risk_report"} & keys
    assert not hasattr(media, "sanitize_media_audit_payload")
    assert not hasattr(media, "write_generated_media")
    assert not resources.files("stackos_connectors").joinpath("connectors/google_paa").is_dir()
    with pytest.raises(ModelError):
        PermissionVerification(evidence_source="provider_probe", enforcement="local_required")
