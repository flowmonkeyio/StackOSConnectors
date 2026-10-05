from dataclasses import fields

import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ConnectorRegistry,
    ConnectorRequest,
    ConnectorResult,
    ValidationError,
)


class FixtureConnector:
    key = "fixture"

    def __init__(self):
        self.calls = []

    def validate(self, request):
        return []

    def estimate_cost_cents(self, request):
        return 3

    async def execute(self, request):
        self.calls.append(request)
        return ConnectorResult(
            output_json={"operation": request.operation, "data": request.input_json}
        )


def setup_client():
    connector = FixtureConnector()
    definition = ActionDefinition(
        connector="fixture",
        key="fixture.send",
        operation="post.message",
        input_schema={
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
            "additionalProperties": False,
        },
        auth_methods=(
            AuthMethodDefinition(
                key="api_key",
                fields_schema={
                    "type": "object",
                    "required": ["token"],
                    "properties": {"token": {"type": "string", "minLength": 1}},
                },
            ),
        ),
        config={"url": "https://fixture.invalid/send"},
        description="Send a message.",
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[definition], implementations={"fixture": lambda: connector}
        )
    )
    return client, connector, definition


@pytest.mark.asyncio
async def test_named_dispatch_and_caller_data_are_preserved():
    client, connector, _ = setup_client()
    data = {"message": "hello"}
    auth = ConnectorAuth("api_key", {"token": "SYNTHETIC_SECRET"})
    result = await client.execute("fixture", "fixture.send", data, auth)
    assert result.output_json == {"operation": "post.message", "data": data}
    assert len(connector.calls) == 1
    assert connector.calls[0].auth.method == "api_key"
    assert connector.calls[0].config_json["url"] == "https://fixture.invalid/send"
    assert data == {"message": "hello"}
    assert client.estimate_cost("fixture", "fixture.send", data, auth) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connector,action,data,auth",
    [
        ("missing", "fixture.send", {"message": "hi"}, None),
        ("fixture", "missing", {"message": "hi"}, None),
        ("fixture", "fixture.send", {}, {"method": "api_key", "fields": {"token": "secret"}}),
        ("fixture", "fixture.send", {"message": "hi"}, None),
        (
            "fixture",
            "fixture.send",
            {"message": "hi"},
            {"method": "oauth", "fields": {"token": "secret"}},
        ),
        ("fixture", "fixture.send", {"message": "hi"}, {"method": "api_key", "fields": {}}),
        (
            "fixture",
            "fixture.send",
            {"message": "hi", "url": "https://other.invalid"},
            {"method": "api_key", "fields": {"token": "secret"}},
        ),
    ],
)
async def test_invalid_selection_data_or_auth_never_dispatches(connector, action, data, auth):
    client, implementation, _ = setup_client()
    with pytest.raises(ConnectorError):
        await client.execute(connector, action, data, auth)
    assert implementation.calls == []


def test_data_only_validation_does_not_need_auth():
    client, implementation, _ = setup_client()
    assert client.validate_data("fixture", "fixture.send", {"message": "hi"}) == []
    assert client.validate_data("fixture", "fixture.send", {})
    assert implementation.calls == []


def test_definitions_are_deeply_immutable_and_no_shadowing():
    client, _, definition = setup_client()
    with pytest.raises(TypeError):
        definition.config["url"] = "https://other.invalid"
    with pytest.raises(TypeError):
        definition.input_schema["properties"]["message"]["type"] = "number"
    with pytest.raises(ValueError):
        client.register_actions([definition])
    scoped = client.register_actions(
        [ActionDefinition(connector="fixture", key="fixture.other", operation="other")]
    )
    assert len(scoped.describe("fixture")["actions"]) == 2
    assert len(client.describe("fixture")["actions"]) == 1
    assert "descriptor" not in {item.name for item in fields(CallOptions)}


def test_neutral_request_has_no_host_state_and_safe_repr():
    auth = ConnectorAuth("api_key", {"token": "SYNTHETIC_SECRET"}, {"password": "SECRET_CONFIG"})
    request = ConnectorRequest(
        "fixture", "fixture.send", "send", {"message": "SECRET_DATA"}, {}, auth
    )
    text = (
        repr(auth)
        + repr(request)
        + repr(CallOptions(provider_context={"secret": "SECRET_CONTEXT"}))
    )
    for secret in ["SYNTHETIC_SECRET", "SECRET_CONFIG", "SECRET_DATA", "SECRET_CONTEXT"]:
        assert secret not in text
    assert not {
        "project_id",
        "plugin_slug",
        "session",
        "credential",
        "action_call_id",
        "run_id",
    } & {item.name for item in fields(ConnectorRequest)}


@pytest.mark.asyncio
async def test_error_partial_output_and_success_echo_redaction():
    client, implementation, _ = setup_client()
    auth = ConnectorAuth("api_key", {"token": "SYNTHETIC_SECRET"})

    async def fail(request):
        raise ConnectorError(
            "echo SYNTHETIC_SECRET",
            provider_status_code=503,
            provider_error={"echo": "SYNTHETIC_SECRET"},
            output_json={"status": "partial", "sent": ["message-1"]},
            metadata_json={"outcome": "unknown", "retry_safe": False},
        )

    implementation.execute = fail
    with pytest.raises(ConnectorError) as caught:
        await client.execute("fixture", "fixture.send", {"message": "hi"}, auth)
    assert "SYNTHETIC_SECRET" not in str(caught.value)
    assert caught.value.provider_error == {"echo": "[redacted]"}
    assert caught.value.output_json["sent"] == ["message-1"]
    assert caught.value.metadata_json == {"outcome": "unknown", "retry_safe": False}

    async def echo(request):
        return ConnectorResult(output_json={"echo": "SYNTHETIC_SECRET"})

    implementation.execute = echo
    assert (
        await client.execute("fixture", "fixture.send", {"message": "hi"}, auth)
    ).output_json == {"echo": "[redacted]"}


def test_schema_errors_do_not_echo_values():
    client, _, _ = setup_client()
    with pytest.raises(ValidationError) as caught:
        client.estimate_cost(
            "fixture", "fixture.send", {"message": {"value": "SYNTHETIC_SECRET"}}, None
        )
    assert "SYNTHETIC_SECRET" not in str(caught.value)


def test_unavailable_implementation_is_truthful_and_lazy():
    registry = ConnectorRegistry(
        actions=[ActionDefinition(connector="future", key="future.send", operation="send")]
    )
    client = ConnectorClient(registry=registry)
    assert client.describe("future")["available"] is False
    with pytest.raises(ConnectorError, match="implementation"):
        client.estimate_cost("future", "future.send", {}, None)


def test_lazy_binding_is_not_imported_by_discovery(monkeypatch):
    from types import SimpleNamespace

    imports = []

    def load(module):
        imports.append(module)
        return SimpleNamespace(Fixture=FixtureConnector)

    monkeypatch.setattr("stackos_connectors.registry.import_module", load)
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[ActionDefinition(connector="fixture", key="fixture.read", operation="read")],
            implementations={"fixture": "fixture_module:Fixture"},
        )
    )
    assert client.describe("fixture")["available"] is True
    assert client.validate_data("fixture", "fixture.read", {}) == []
    assert imports == []
    assert client.estimate_cost("fixture", "fixture.read", {}) == 3
    assert imports == ["fixture_module"]


@pytest.mark.asyncio
async def test_non_json_data_never_dispatches():
    client, implementation, _ = setup_client()
    with pytest.raises(ValidationError):
        await client.execute(
            "fixture",
            "fixture.send",
            {"message": object()},
            ConnectorAuth("api_key", {"token": "secret"}),
        )
    assert implementation.calls == []
