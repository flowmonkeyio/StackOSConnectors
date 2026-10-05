from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from stackos_connectors import (
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ValidationError,
)
from stackos_connectors.catalog import load_registry

CASES = json.loads((Path(__file__).parent / "fixtures/wire-contracts.json").read_text())
PROVIDERS = sorted({case["connector"] for case in CASES})


@pytest.fixture
def client():
    return ConnectorClient(
        registry=load_registry(
            *("connectors/" + name.replace("-", "_") + "/catalog.json" for name in PROVIDERS)
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case["action"] for case in CASES])
async def test_named_action_preserves_original_wire_contract_and_result(client, case):
    calls = []
    expected = case["request"]

    def respond(request):
        calls.append(request)
        assert request.method == expected["method"]
        assert str(request.url) == expected["url"]
        for name, value in expected["headers"].items():
            assert request.headers[name] == value
        if expected["json"] is not None:
            assert json.loads(request.content) == expected["json"]
        elif expected["form"] is not None:
            assert parse_qs(request.content.decode()) == parse_qs(expected["content"])
        else:
            assert request.content == b""
        assert request.extensions["timeout"]["read"] == 60.0
        return httpx.Response(
            200, json=case["response"], headers={"x-request-id": "request-fixture"}
        )

    data = copy.deepcopy(case["data"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await client.execute(
            case["connector"],
            case["action"],
            data,
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http),
        )
    assert len(calls) == 1
    assert result.model_dump(exclude={"files"}) == case["result"]
    assert data == case["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case["action"] for case in CASES])
async def test_bad_selection_auth_or_data_never_dispatches(client, case):
    calls = []

    def forbidden(request):
        calls.append(request)
        raise AssertionError("validation dispatched HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        options = CallOptions(http=http)
        for action, data, auth in [
            ("not-a-declared-action", case["data"], ConnectorAuth(**case["auth"])),
            (case["action"], [], ConnectorAuth(**case["auth"])),
            (case["action"], case["data"], ConnectorAuth("unsupported", case["auth"]["fields"])),
            (case["action"], case["data"], ConnectorAuth(case["auth"]["method"], {})),
        ]:
            with pytest.raises(ValidationError):
                await client.execute(case["connector"], action, data, auth, options)
    assert calls == []


def test_full_owned_inventory_native_schemas_and_host_only_exclusions(client):
    assert client.list_connectors() == PROVIDERS
    assert len(client.registry.actions) == 53
    assert {key for _, key in client.registry.actions} == {case["action"] for case in CASES}
    for provider in PROVIDERS:
        description = client.describe(provider)
        assert description["name"] and description["description"]
        for action in description["actions"]:
            assert action["description"] and action["guidance"]
            assert not any(
                name.endswith("_ref") for name in action["input_schema"].get("properties", {})
            )
            for method in action["auth_methods"]:
                assert "client_secret" not in method["fields_schema"].get("required", [])
    for provider, action in [
        ("clay", "clay.workflow.result.ingest"),
        ("meta-ads", "meta.asset.upload"),
    ]:
        assert "deferred_actions" not in client.describe(provider)
        with pytest.raises(ValidationError):
            client.registry.action(provider, action)


def test_agent_discovery_retains_icons_names_setup_and_native_contracts(client):
    from importlib.resources import files

    for provider in PROVIDERS:
        description = client.describe(provider)
        icon = description["icon"]
        assert files("stackos_connectors").joinpath(icon["path"]).read_bytes()
        assert icon["media_type"].startswith("image/")
        assert description["config"]["setup"]["docs_url"].startswith("https://")
        for method in description["auth_methods"]:
            assert method["description"] and method["setup"]["description"]
            assert "permission_verification" not in method["setup"]
            assert not any(f["key"].endswith("_ref") for f in method["setup"]["fields"])
        for action in description["actions"]:
            assert action["name"] and action["description"] and action["guidance"]
            assert action["icon"] == icon
            assert all(method["setup"]["description"] for method in action["auth_methods"])
        text = json.dumps(description).lower()
        for marker in [
            "stackos connection",
            "daemon-side",
            "safe refs",
            "saved-reference",
            "permission_verification",
            "locally scope-gated",
            "allowlisted query templates",
        ]:
            assert marker not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_explicit_timeout_is_applied_to_supplied_client_without_mutation(client, provider):
    case = next(case for case in CASES if case["connector"] == provider)
    seen = []

    def respond(request):
        seen.append(request)
        assert request.extensions["timeout"]["read"] == 7.5
        return httpx.Response(200, json=case["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), timeout=19) as http:
        await client.execute(
            provider,
            case["action"],
            case["data"],
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http, timeout=7.5),
        )
        assert http.timeout.read == 19 and not http.is_closed
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,method,field,expected_header",
    [
        ("pipedrive", "api_token", "api_token", "x-api-token"),
        ("pipedrive", "oauth2_authorization_code", "access_token", "authorization"),
        ("pipedrive", "oauth2_token", "access_token", "authorization"),
        ("salesloft", "api_key", "api_key", "authorization"),
        ("salesloft", "oauth2_authorization_code", "access_token", "authorization"),
        ("salesloft", "oauth2_token", "access_token", "authorization"),
    ],
)
async def test_saved_method_selects_transport_not_present_token_fields(
    client, provider, method, field, expected_header
):
    case = next(item for item in CASES if item["connector"] == provider)
    fields = {"api_key": "wrong-api", "api_token": "wrong-api-token", "access_token": "wrong-oauth"}
    fields[field] = "selected-secret"
    config = {**case["auth"]["config"], "auth_method_key": "irrelevant-conflicting-config"}
    calls = []

    def respond(request):
        calls.append(request)
        expected = (
            "selected-secret" if expected_header == "x-api-token" else "Bearer selected-secret"
        )
        assert request.headers[expected_header] == expected
        if provider == "pipedrive":
            assert ("authorization" in request.headers) is (expected_header == "authorization")
            assert ("x-api-token" in request.headers) is (expected_header == "x-api-token")
        return httpx.Response(200, json={"id": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        await client.execute(
            provider,
            case["action"],
            case["data"],
            ConnectorAuth(method, fields, config),
            CallOptions(http=http),
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_google_ads_two_page_cursor_survives_redaction(client):
    case = next(item for item in CASES if item["action"] == "google.report.search")
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [{"page": len(sent)}],
                **({"nextPageToken": "second-page"} if len(sent) == 1 else {}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        data = {"customer_id": "123", "query": "SELECT campaign.id FROM campaign"}
        first = await client.execute(
            "google-ads",
            case["action"],
            data,
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http),
        )
        data["page_cursor"] = first.output_json["body"]["next_page_cursor"]
        await client.execute(
            "google-ads",
            case["action"],
            data,
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http),
        )
    assert sent == [{"query": data["query"]}, {"query": data["query"], "pageToken": "second-page"}]


@pytest.mark.asyncio
async def test_provider_failure_preserves_status_receipt_and_redacts_secret_without_retry(client):
    case = next(item for item in CASES if item["connector"] == "outreach")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            429,
            json={"echo": "canary-access_token", "error": "limited"},
            headers={"x-request-id": "provider-receipt", "retry-after": "2"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ConnectorError) as error:
            await client.execute(
                case["connector"],
                case["action"],
                case["data"],
                ConnectorAuth(**case["auth"]),
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert error.value.provider_status_code == 429
    assert error.value.metadata_json["request_id"] == "provider-receipt"
    assert error.value.metadata_json["retry_after"] == "2"
    assert "canary-access_token" not in repr(error.value.provider_error)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_explicit_timeout_is_forwarded(client, provider):
    case = next(item for item in CASES if item["connector"] == provider)

    def respond(request):
        assert request.extensions["timeout"]["read"] == 7.0
        return httpx.Response(200, json=case["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        await client.execute(
            provider,
            case["action"],
            case["data"],
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http, timeout=7.0),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,data",
    [
        ("google-workspace.gmail.message.send", {"message": {"raw": "SGVsbG8"}}),
        (
            "google-workspace.calendar.event.create",
            {
                "calendar_id": "primary",
                "event": {"start": {}, "end": {}},
            },
        ),
        (
            "google-workspace.calendar.event.create",
            {
                "calendar_id": "shared@example.test",
                "event": {"start": {}, "end": {}, "attendees": [{"email": "someone@example.test"}]},
            },
        ),
    ],
)
async def test_workspace_direct_service_account_guards_do_not_send(client, action, data):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as http:
        with pytest.raises(ValidationError) as error:
            await client.execute(
                "google-workspace",
                action,
                data,
                ConnectorAuth("service-account", {"access_token": "workspace-canary"}),
                CallOptions(http=http),
            )
    assert calls == []
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.asyncio
async def test_workspace_delegated_service_account_sends_with_resolved_token(client):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer delegated-token"
        return httpx.Response(200, json={"id": "message-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        await client.execute(
            "google-workspace",
            "google-workspace.gmail.message.send",
            {"message": {"raw": "SGVsbG8"}},
            ConnectorAuth(
                "service-account",
                {"access_token": "delegated-token"},
                {"delegated_subject": "user@example.test"},
            ),
            CallOptions(http=http),
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/table",
        "https://localhost/hook",
        "https://10.0.0.1/hook",
        "https://user:password@example.test/hook",
    ],
)
async def test_clay_unsafe_webhook_is_rejected_without_send(client, url):
    case = next(item for item in CASES if item["connector"] == "clay")
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as http:
        with pytest.raises(ValidationError):
            await client.execute(
                "clay",
                case["action"],
                case["data"],
                ConnectorAuth(case["auth"]["method"], {}, {"webhook_url": url}),
                CallOptions(http=http),
            )
    assert calls == []


@pytest.mark.asyncio
async def test_apollo_master_requirement_preserves_diagnostic_without_send(client):
    case = next(item for item in CASES if item["action"] == "apollo.people.search")
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as http:
        with pytest.raises(ValidationError) as error:
            await client.execute(
                "apollo",
                case["action"],
                case["data"],
                ConnectorAuth(
                    case["auth"]["method"],
                    {"api_key": "apollo-canary"},
                    {"access_scope": "endpoint"},
                ),
                CallOptions(http=http),
            )
    assert calls == []
    assert error.value.data["provider"] == "apollo"
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.asyncio
async def test_google_ads_partial_failure_flags_and_response_survive(client):
    case = next(item for item in CASES if item["operation"] == "conversion_upload.clicks")
    data = {
        **case["data"],
        "partial_failure": True,
        "validate_only": True,
        "debug_enabled": True,
        "job_id": 42,
    }
    response = {
        "results": [{"gclid": "accepted"}],
        "partialFailureError": {"code": 3, "message": "one conversion rejected"},
    }

    def respond(request):
        body = json.loads(request.content)
        assert body == {
            "conversions": data["conversions"],
            "partialFailure": True,
            "validateOnly": True,
            "debugEnabled": True,
            "jobId": 42,
        }
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await client.execute(
            "google-ads",
            case["action"],
            data,
            ConnectorAuth(**case["auth"]),
            CallOptions(http=http),
        )
    assert result.output_json["body"] == response
    assert result.cost_cents == 0


NUMERIC_CASES = [
    case
    for case in CASES
    if any(
        key.endswith("_id") and key != "job_id" and isinstance(value, str) and value.isdigit()
        for key, value in case["data"].items()
    )
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", NUMERIC_CASES, ids=[case["action"] for case in NUMERIC_CASES])
async def test_native_integer_ids_keep_wire_types_and_boolean_ids_fail_closed(client, case):
    data = copy.deepcopy(case["data"])
    definition = client.registry.action(case["connector"], case["action"])
    fields = [
        key
        for key in data
        if definition.input_schema["properties"].get(key, {}).get("type") == ("string", "integer")
    ]
    assert fields
    for key in fields:
        data[key] = int(data[key])
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url) == case["request"]["url"]
        if case["connector"] == "salesloft":
            assert json.loads(request.content) == {"cadence_id": 123, "person_id": 123}
        return httpx.Response(200, json=case["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        auth = ConnectorAuth(**case["auth"])
        await client.execute(case["connector"], case["action"], data, auth, CallOptions(http=http))
        for key in fields:
            with pytest.raises(ValidationError):
                await client.execute(
                    case["connector"],
                    case["action"],
                    {**data, key: True},
                    auth,
                    CallOptions(http=http),
                )
    assert len(calls) == 1
