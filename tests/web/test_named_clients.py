"""Named public package dispatch using selected plain authentication."""

import importlib
import json

import httpx
import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ConnectorRegistry,
    ValidationError,
)

from .catalog_fixture import PROVIDERS, client_for, document


@pytest.mark.asyncio
async def test_ahrefs_guard_records_limits_read_and_no_paid_request():
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == "https://api.ahrefs.com/v3/subscription-info/limits-and-usage"
        return httpx.Response(200, json={"limits_and_usage": {"subscription": "Lite"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValidationError) as caught:
            await client_for("ahrefs").execute(
                "ahrefs",
                "competitor.keywords",
                {"target": "example.test", "limit": 101, "date": "2026-06-15"},
                ConnectorAuth("api_key", {"api_key": "synthetic-ahrefs-key"}),
                CallOptions(http=http),
            )
    assert caught.value.data["effective_row_limit"] == 100
    assert caught.value.metadata_json["provider_executed"] is True
    assert caught.value.metadata_json["primary_request_executed"] is False
    assert caught.value.metadata_json["retry_safe"] is True
    assert len(seen) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
def test_catalog_native_contracts_and_lazy_implementation(provider):
    doc = document(provider)
    for method in doc["auth_methods"]:
        assert method["payload_format"] in {"json", "raw"}
        if method["payload_format"] == "raw":
            assert method["payload_field"]
        assert "type" in method["fields_schema"]
    if provider in {"google-paa", "openrouter"}:
        assert not doc["actions"] and "implementation" not in doc
        return
    module, symbol = doc["implementation"].split(":")
    implementation = getattr(importlib.import_module(module), symbol)
    assert implementation.key == provider
    assert (
        len(client_for(provider).describe(provider)["actions"]) == len(doc["actions"])
        if doc["actions"]
        else provider == "http"
    )
    for action in doc["actions"]:
        assert "operation" not in action["config"]
        assert "connector" not in action["config"]
        assert action["description"]
        assert all(
            key in {item["key"] for item in doc["auth_methods"]} for key in action["auth_methods"]
        )


@pytest.mark.asyncio
async def test_serper_named_action_wire_and_exact_secret_echo_redaction():
    seen = []
    secret = "SYNTHETIC-SERPER-KEY"

    def handle(request):
        seen.append(request)
        assert request.headers["X-API-KEY"] == secret
        assert json.loads(request.content) == {"q": "test", "num": 10, "page": 2, "gl": "us"}
        return httpx.Response(200, json={"organic": [{"title": "okay"}], "diagnostic": secret})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = client_for("serper")
        auth = ConnectorAuth(document("serper")["auth_methods"][0]["key"], {"api_key": secret})
        result = await client.execute(
            "serper",
            "serper.search",
            {"query": "test", "page": 2, "country": "us"},
            auth,
            CallOptions(http=http),
        )
        assert result.output_json["organic"] == [{"title": "okay"}]
        assert secret not in result.model_dump_json()
        assert not http.is_closed
        for action, data, selected_auth in [
            ("missing", {"query": "test"}, auth),
            ("serper.search", {}, auth),
            ("serper.search", {"query": "test"}, None),
        ]:
            with pytest.raises(ValidationError):
                await client.execute("serper", action, data, selected_auth, CallOptions(http=http))
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_shopify_named_static_asset_request_and_cost():
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == "https://demo.myshopify.com/admin/api/2026-07/graphql.json"
        assert request.headers["X-Shopify-Access-Token"] == "synthetic-shopify-token"
        body = json.loads(request.content)
        assert "query ListProducts" in body["query"]
        assert body["variables"] == {"first": 5, "query": 'status:active AND vendor:"Acme"'}
        return httpx.Response(
            200,
            json={
                "data": {"products": {"edges": [], "pageInfo": {"hasNextPage": False}}},
                "extensions": {"cost": {"actualQueryCost": 2}},
            },
        )

    auth = ConnectorAuth(
        document("shopify")["auth_methods"][0]["key"],
        {"admin_api_access_token": "synthetic-shopify-token"},
        {"store_domain": "demo.myshopify.com", "api_version": "2026-07"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for("shopify").execute(
            "shopify",
            "list_products",
            {"limit": 5, "status": "ACTIVE", "vendor": "Acme"},
            auth,
            CallOptions(http=http),
        )
    assert result.output_json["data"]["products"]["edges"] == []
    assert result.metadata_json["cost"]["actualQueryCost"] == 2
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
async def test_jina_anonymous_and_supplied_auth_protocol(authenticated):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers.get("authorization") == (
            "Bearer synthetic-jina-token" if authenticated else None
        )
        return httpx.Response(200, text="Reader content")

    auth = ConnectorAuth("api_key", {"api_key": "synthetic-jina-token"}) if authenticated else None
    # Both paths use the same catalog-loaded optional-auth definition.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for("jina", anonymous=not authenticated).execute(
            "jina", "web.read", {"url": "https://example.test/page"}, auth, CallOptions(http=http)
        )
    assert "Reader content" in str(result.output_json)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,action,data,url",
    [
        (
            "google-analytics",
            "ga4.properties.metadata.get",
            {"property_id": "123"},
            "https://analyticsdata.googleapis.com/v1beta/properties/123/metadata",
        ),
        (
            "google-tag-manager",
            "google-tag-manager.workspace.tags.list",
            {"account_id": "1", "container_id": "2", "workspace_id": "3"},
            (
                "https://tagmanager.googleapis.com/tagmanager/v2/ac"
                "counts/1/containers/2/workspaces/3/tags"
            ),
        ),
    ],
)
async def test_google_provider_ids_wire_without_host_aliases(provider, action, data, url):
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == url
        assert request.headers["authorization"] == "Bearer synthetic-google-token"
        return httpx.Response(200, json={"nextPageToken": "next-page"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for(provider).execute(
            provider,
            action,
            data,
            ConnectorAuth("service-account", {"access_token": "synthetic-google-token"}),
            CallOptions(http=http),
        )
    assert result.output_json["next_page_cursor"] == "next-page"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_reddit_uses_resolved_token_and_user_agent():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer resolved-reddit-token"
        assert request.headers["user-agent"] == "caller-test/1.0"
        return httpx.Response(200, json={"data": {"children": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client_for("reddit").execute(
            "reddit",
            "reddit.search-subreddit",
            {"subreddit": "python", "query": "library"},
            ConnectorAuth(
                "client_credentials",
                {"access_token": "resolved-reddit-token", "user_agent": "caller-test/1.0"},
            ),
            CallOptions(http=http),
        )
    assert result.output_json["data"]["children"] == []
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body", [(429, "token SYNTHETIC-HTTP-KEY"), (200, "invalid JSON SYNTHETIC-HTTP-KEY")]
)
async def test_http_received_response_is_never_reported_as_no_dispatch(status, body):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(status, text=body)

    auth_method = AuthMethodDefinition("bearer", {"type": "object", "required": ["token"]})
    definition = ActionDefinition(
        "http",
        "caller.webhook",
        "request",
        auth_methods=(auth_method,),
        config={
            "http": {
                "url": "https://example.test/hook",
                "method": "POST",
                "response_mode": "json",
                "timeout_s": 7,
                "auth": {"type": "bearer"},
            }
        },
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[definition],
            implementations={
                "http": "stackos_connectors.connectors.http.actions:HttpActionConnector"
            },
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as caught:
            await client.execute(
                "http",
                "caller.webhook",
                {"message": "hello"},
                ConnectorAuth("bearer", {"token": "SYNTHETIC-HTTP-KEY"}),
                CallOptions(http=http),
            )
    assert caught.value.provider_status_code == status
    assert caught.value.metadata_json["provider_executed"] is True
    assert caught.value.metadata_json["retry_safe"] is False
    assert "SYNTHETIC-HTTP-KEY" not in str(caught.value.provider_error)
    assert len(seen) == 1
