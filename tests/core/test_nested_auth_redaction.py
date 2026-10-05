import pytest
from test_contract import setup_client

from stackos_connectors import ConnectorAuth, ConnectorError, ConnectorResult, ValidationIssue

SECRET = "SYNTHETIC_NESTED_CONFIG_SECRET"
AUTH = ConnectorAuth(
    "api_key",
    {"token": "synthetic-token"},
    {
        "proxy": {"password": SECRET, "url": "https://proxy.example"},
        "connections": [{"credentials": {"value": "SYNTHETIC_DEEP_SECRET"}}],
        "region": "us-east-1",
    },
)


def assert_redacted(value):
    text = repr(value)
    assert SECRET not in text
    assert "SYNTHETIC_DEEP_SECRET" not in text


@pytest.mark.asyncio
async def test_nested_config_secrets_redacted_from_output():
    client, implementation, _ = setup_client()

    async def echo(request):
        return ConnectorResult(
            output_json={
                "echo": request.auth.config["proxy"]["password"],
                "deep_echo": request.auth.config["connections"][0]["credentials"]["value"],
                "url": request.auth.config["proxy"]["url"],
                "region": request.auth.config["region"],
            }
        )

    implementation.execute = echo
    result = await client.execute("fixture", "fixture.send", {"message": "hi"}, AUTH)
    assert_redacted(result.output_json)
    assert result.output_json["url"] == "https://proxy.example"
    assert result.output_json["region"] == "us-east-1"


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
def test_nested_config_secrets_redacted_from_preflight_issues(entry):
    client, implementation, _ = setup_client()
    implementation.validate = lambda request: [ValidationIssue(path="$", message=SECRET)]
    if entry == "validate":
        assert_redacted(client.validate("fixture", "fixture.send", {"message": "hi"}, AUTH))
    else:
        with pytest.raises(ConnectorError) as caught:
            client.estimate_cost("fixture", "fixture.send", {"message": "hi"}, AUTH)
        assert_redacted(vars(caught.value))
        assert caught.value.metadata_json["provider_executed"] is False
    assert implementation.calls == []


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
def test_nested_config_secrets_redacted_from_preflight_errors(entry):
    client, implementation, _ = setup_client()

    def fail(request):
        raise ConnectorError(
            SECRET, provider_error={"echo": SECRET}, metadata_json={"echo": "SYNTHETIC_DEEP_SECRET"}
        )

    if entry == "validate":
        implementation.validate = fail
    else:
        implementation.estimate_cost_cents = fail
    with pytest.raises(ConnectorError) as caught:
        getattr(client, entry)("fixture", "fixture.send", {"message": "hi"}, AUTH)
    assert_redacted(vars(caught.value))
    assert caught.value.metadata_json["provider_executed"] is False
    assert implementation.calls == []
