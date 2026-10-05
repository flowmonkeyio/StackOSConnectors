"""Public named Trackbooth actions, immutable declarations and exact wire proof."""

import json
import traceback
from importlib.resources import files
from urllib.parse import parse_qs

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.trackbooth import declare_action
from stackos_connectors.connectors.trackbooth.assets import TrackboothAssets
from stackos_connectors.connectors.trackbooth.integration import TrackboothIntegration
from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError


def client():
    return ConnectorClient(registry=load_registry("connectors/trackbooth/catalog.json"))


AUTH = ConnectorAuth(
    "api-key", {"api_key": "synthetic-api-key"}, {"api_base_url": "https://example.test/v1"}
)


def endpoint(method="POST"):
    return {
        "operation_id": "FixturesController.create",
        "method": method,
        "path": "/api/fixtures/{id}",
        "context": {"title": "Create fixture", "subtitle": "Create a provider fixture."},
        "path_params": [{"name": "id"}],
        "query_schema": {"json_schema": {"type": "object", "properties": {}}},
        "body_schema": {
            "json_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "mode": {"enum": ["one", "two"]}},
            }
        }
        if method != "GET"
        else None,
    }


@pytest.mark.parametrize("operation", ["catalog.list", "catalog.export", "operation.describe"])
async def test_fixed_catalog_wire(operation):
    called = []
    descriptor = endpoint()
    data = (
        {"operation_id": "FixturesController.create"} if operation == "operation.describe" else {}
    )
    suffix = {
        "catalog.list": "",
        "catalog.export": "/export",
        "operation.describe": "/FixturesController.create",
    }[operation]
    reply = (
        {"data": descriptor}
        if operation == "operation.describe"
        else {"data": {"endpoints": [descriptor], "catalog_hash": "hash", "endpoint_count": 1}}
        if operation == "catalog.export"
        else {"data": [descriptor]}
    )

    def handle(request):
        called.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://example.test/v1/api/agent-api/catalog" + suffix
        assert request.headers["x-api-key"] == "synthetic-api-key"
        assert request.headers["x-acting-as-account"] == "acct-other"
        assert "authorization" not in request.headers and request.content == b""
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=reply, headers={"x-request-id": "request-one"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=91) as http:
        result = await client().execute(
            "trackbooth",
            "trackbooth." + operation,
            data,
            AUTH,
            CallOptions(
                http=http, timeout=7, provider_context={"acting_as_account": " acct-other "}
            ),
        )
        assert not http.is_closed and http.timeout.read == 91
    assert len(called) == 1
    assert result.metadata_json["provider_executed"] is True
    assert result.metadata_json["request_id"] == "request-one"
    assert "execution_blocked" not in str(result.output_json)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_explicit_declaration_exact_request_and_frozen_route(method):
    descriptor = endpoint(method)
    declaration = declare_action("trackbooth.fixture", descriptor)
    bound = client().register_actions([declaration])
    descriptor["path"] = "/tampered"
    descriptor["method"] = "GET" if method != "GET" else "DELETE"
    with pytest.raises(TypeError):
        declaration.config["path"] = "/tampered"
    payload = {
        "path_params": {"id": "a/b c"},
        "query": {"include": ["one", "two"], "active": True, "empty": None, "nested": {"a": 1}},
    }
    if method != "GET":
        payload["body"] = {"name": "fixture", "mode": "one"}
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == method
        assert request.url.raw_path.split(b"?")[0] == b"/v1/api/fixtures/a%2Fb%20c"
        assert parse_qs(request.url.query.decode()) == {
            "include": ["one", "two"],
            "active": ["true"],
            "nested": ['{"a":1}'],
        }
        assert request.headers["x-api-key"] == "synthetic-api-key"
        assert "x-acting-as-account" not in request.headers
        assert (json.loads(request.content) if request.content else None) == payload.get("body")
        assert request.extensions["timeout"]["read"] == 91
        return httpx.Response(201, json={"id": "native-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=91) as http:
        result = await bound.execute(
            "trackbooth", "trackbooth.fixture", payload, AUTH, CallOptions(http=http)
        )
        assert not http.is_closed
    assert len(calls) == 1
    assert result.output_json["path"] == "/api/fixtures/{id}"
    assert result.output_json["method"] == method
    assert result.output_json["data"] == {"id": "native-id"}


@pytest.mark.parametrize(
    "extra",
    [
        {"method": "DELETE"},
        {"path": "/wrong"},
        {"operation_id": "Wrong"},
        {"headers": {"x-api-key": "wrong"}},
        {"acting_as_account": "wrong"},
    ],
)
async def test_payload_cannot_override_registration(extra):
    bound = client().register_actions([declare_action("trackbooth.fixture", endpoint())])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "trackbooth",
                "trackbooth.fixture",
                {"path_params": {"id": "1"}, "body": {"name": "x"}, **extra},
                AUTH,
                CallOptions(http=http),
            )


def test_registration_cannot_shadow_builtin_and_does_not_install_snapshot():
    original = client()
    assert len(original.registry.actions) == 3
    with pytest.raises(ValueError, match="duplicate action"):
        original.register_actions([declare_action("trackbooth.catalog.list", endpoint())])
    assert len(original.registry.actions) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"method": "CONNECT"},
        {"path": "https://other.test"},
        {"path": "//other.test"},
        {"path": "/path?token=x"},
        {"operation_id": ""},
    ],
)
def test_invalid_declaration_fails_locally(bad):
    with pytest.raises(ValidationError):
        declare_action("trackbooth.fixture", {**endpoint(), **bad})


@pytest.mark.parametrize("status", [403, 429, 503])
async def test_provider_errors_preserve_body_and_single_write(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"code": "provider-error", "retry_after_ms": 45})

    bound = client().register_actions([declare_action("trackbooth.fixture", endpoint())])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "trackbooth",
                "trackbooth.fixture",
                {"path_params": {"id": "1"}, "body": {"name": "x"}},
                AUTH,
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.provider_status_code == status
    assert failed.value.provider_error == {"code": "provider-error", "retry_after_ms": 45}
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["retry_safe"] is False


async def test_transport_write_failure_is_unknown_and_never_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadError("private-server-text", request=request)

    bound = client().register_actions([declare_action("trackbooth.fixture", endpoint())])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "trackbooth",
                "trackbooth.fixture",
                {"path_params": {"id": "1"}, "body": {"name": "x"}},
                AUTH,
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert "private-server-text" not in str(failed.value)


async def test_redirect_not_followed_and_caller_client_unchanged():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "https://other.test/steal"}, text="redirect"
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        await client().execute(
            "trackbooth", "trackbooth.catalog.list", {}, AUTH, CallOptions(http=http)
        )
        assert http.follow_redirects is True and not http.is_closed
    assert len(calls) == 1


async def test_native_probe_exact_auth_catalog():
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://example.test/api/agent-api/catalog"
        assert request.method == "GET" and request.content == b""
        assert request.headers["x-api-key"] == "synthetic-api-key"
        assert "x-acting-as-account" not in request.headers
        return httpx.Response(200, json={"data": {"endpoints": [{"operation_id": "Fixture.read"}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await TrackboothIntegration(
            payload=b'{"api_key":"synthetic-api-key"}',
            http=http,
            api_base_url="https://example.test",
        ).test_credentials()
        assert not http.is_closed
    assert result["ok"] is True and result["endpoint_count"] == 1 and len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 500, "transport", 200])
@pytest.mark.parametrize("echo_in_base", [False, True])
async def test_native_probe_redacts_exact_auth_echo_every_controlled_surface(status, echo_in_base):
    secret = "fixtureOpaqueA71xQ9"
    base = "https://example.test" + (f"/{secret}" if echo_in_base else "")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET" and request.content == b""
        assert str(request.url) == base + "/api/agent-api/catalog"
        assert request.headers["x-api-key"] == secret
        assert "x-acting-as-account" not in request.headers
        assert request.extensions["timeout"]["read"] == 19
        if status == "transport":
            raise httpx.ReadError("Rejected supplied value " + secret, request=request)
        if status == 200:
            return httpx.Response(200, json={"data": [{"note": secret}]})
        return httpx.Response(status, text="Rejected supplied value " + secret)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), timeout=19, follow_redirects=True
    ) as http:
        probe = TrackboothIntegration(
            payload=json.dumps({"api_key": secret}).encode(), http=http, api_base_url=base
        )
        if status in {500, "transport"}:
            with pytest.raises(IntegrationDownError) as failed:
                await probe.test_credentials()
            error = failed.value
            surfaces = {"nested": {"detail": error.detail, "data": error.data}}
            assert secret not in json.dumps(surfaces)
            assert secret not in str(error) and secret not in repr(error)
            assert secret not in "".join(traceback.format_exception(error))
            if status == "transport":
                assert error.__cause__ is None and error.__suppress_context__
            else:
                assert error.data["status"] == 500
        else:
            result = await probe.test_credentials()
            assert secret not in json.dumps({"nested": [result]}) and secret not in repr(result)
            assert result["ok"] is (status == 200)
            if status == 200:
                assert result["endpoint_count"] == 1 and result["status"] == "ok"
            else:
                assert result["status_code"] == status
                assert result["status"] == ("unauthorized" if status == 401 else "forbidden")
                assert result["summary"] == "Rejected supplied value [redacted]"
        assert not http.is_closed and http.timeout.read == 19 and http.follow_redirects
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_native_probe_scrubs_before_truncating_diagnostic(status):
    secret = "fixtureOpaqueA71xQ9"
    prefix = "Provider diagnostic " + "x" * 270
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=prefix + secret))
    ) as http:
        probe = TrackboothIntegration(payload=secret.encode(), http=http)
        if status == 500:
            with pytest.raises(IntegrationDownError) as failed:
                await probe.test_credentials()
            diagnostic = failed.value.detail
        else:
            diagnostic = (await probe.test_credentials())["summary"]
        assert secret[:10] not in diagnostic
        assert diagnostic.endswith("[redacted]")


def test_catalog_metadata_assets_and_no_host_policy():
    description = client().describe("trackbooth")
    assert description["name"] and description["description"]
    assert description["icon"]["kind"] == "wordmark"
    assert files("stackos_connectors").joinpath(description["icon"]["path"]).read_bytes()
    for action in description["actions"]:
        assert action["name"] and action["description"] and action["icon"] == description["icon"]
    assets = TrackboothAssets()
    assert assets.catalog and assets.openapi and assets.stackos_tools
    assert not hasattr(assets, "schema_audit") and not hasattr(assets, "is_blocked")
    detail = assets.detail("AccountApiKeyController.revealApiKey")
    assert "execution_blocked" not in detail
    assert len(client().registry.actions) == 3


@pytest.mark.parametrize(
    "auth",
    [
        None,
        ConnectorAuth("unknown", {"api_key": "synthetic-api-key"}),
        ConnectorAuth(
            "api-key", {"api_key": "synthetic-api-key"}, {"api_base_url": "http://10.0.0.1"}
        ),
    ],
)
async def test_invalid_auth_or_base_url_never_sends(auth):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await client().execute(
                "trackbooth", "trackbooth.catalog.list", {}, auth, CallOptions(http=http)
            )
