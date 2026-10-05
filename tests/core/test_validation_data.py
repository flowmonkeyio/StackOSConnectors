"""Structured preflight diagnostics survive without secret or outcome spoofing."""

import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    ConnectorAuth,
    ConnectorClient,
    ConnectorRegistry,
    ValidationError,
)


def diagnostic():
    return ValidationError(
        "request exceeds provider plan",
        data={
            "vendor": "fixture",
            "requested_limit": 200,
            "effective_row_limit": 100,
            "echo": "private-token",
            "provider_executed": True,
        },
    )


def client_for(phase):
    class Fixture:
        key = "fixture"
        calls = 0

        def validate(self, request):
            if phase == "validate":
                raise diagnostic()
            return []

        def estimate_cost_cents(self, request):
            raise diagnostic()

        async def execute(self, request):
            # Provider guards may run at execute entry before any external call.
            raise diagnostic()

    implementation = Fixture()
    definition = ActionDefinition(
        connector="fixture",
        key="fixture.read",
        operation="read",
        auth_methods=(AuthMethodDefinition("api_key"),),
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[definition],
            implementations={"fixture": lambda: implementation},
        )
    )
    return client, implementation


def assert_diagnostics(error):
    assert error.data["vendor"] == "fixture"
    assert error.data["requested_limit"] == 200
    assert error.data["effective_row_limit"] == 100
    assert "private-token" not in repr(error.data)
    assert "private-token" not in repr(error.metadata_json)
    assert error.metadata_json["provider_executed"] is False
    assert error.metadata_json["data"] == error.data


@pytest.mark.parametrize("phase", ["validate", "cost"])
def test_cost_diagnostics_are_preserved_and_sanitized(phase):
    client, implementation = client_for(phase)
    with pytest.raises(ValidationError) as error:
        client.estimate_cost(
            "fixture", "fixture.read", {}, ConnectorAuth("api_key", {"value": "private-token"})
        )
    assert_diagnostics(error.value)
    assert implementation.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["validate", "execute"])
async def test_execution_diagnostics_are_preserved_and_sanitized(phase):
    client, implementation = client_for(phase)
    with pytest.raises(ValidationError) as error:
        await client.execute(
            "fixture", "fixture.read", {}, ConnectorAuth("api_key", {"value": "private-token"})
        )
    assert_diagnostics(error.value)
    assert implementation.calls == 0


def test_validation_diagnostics_remain_secret_safe():
    client, implementation = client_for("validate")
    issues = client.validate(
        "fixture", "fixture.read", {}, ConnectorAuth("api_key", {"value": "private-token"})
    )
    assert issues[0].message == "request exceeds provider plan"
    assert "private-token" not in repr(issues)
    assert implementation.calls == 0
