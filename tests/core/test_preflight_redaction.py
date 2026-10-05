import pytest
from test_contract import setup_client

from stackos_connectors import (
    ConnectorAuth,
    ConnectorError,
    ValidationError,
    ValidationIssue,
)

TOKEN = "SYNTHETIC_PREFLIGHT_SECRET"
AUTH = ConnectorAuth("api_key", {"token": TOKEN})


def assert_safe_error(error):
    assert TOKEN not in repr(vars(error))
    assert TOKEN not in str(error)
    assert error.metadata_json["provider_executed"] is False
    assert error.metadata_json.get("outcome") != "unknown"


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
def test_returned_validation_issues_are_redacted(entry):
    client, implementation, _ = setup_client()
    implementation.validate = lambda request: [
        ValidationIssue(path=f"$.{TOKEN}", message=f"bad {TOKEN}; password=OTHER_SECRET")
    ]
    if entry == "validate":
        issues = client.validate("fixture", "fixture.send", {"message": "hi"}, AUTH)
        assert TOKEN not in repr(issues)
        assert "OTHER_SECRET" not in repr(issues)
    else:
        with pytest.raises(ValidationError) as caught:
            client.estimate_cost("fixture", "fixture.send", {"message": "hi"}, AUTH)
        assert_safe_error(caught.value)
        assert "OTHER_SECRET" not in repr(vars(caught.value))
    assert implementation.calls == []


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
@pytest.mark.parametrize("typed", [False, True])
def test_validation_exceptions_are_safe_and_preflight(entry, typed):
    client, implementation, _ = setup_client()

    def fail(request):
        if typed:
            raise ConnectorError(
                f"bad {TOKEN}",
                provider_error={"echo": TOKEN},
                output_json={"echo": TOKEN},
                metadata_json={"echo": TOKEN},
            )
        raise RuntimeError(TOKEN)

    implementation.validate = fail
    with pytest.raises(ConnectorError) as caught:
        getattr(client, entry)("fixture", "fixture.send", {"message": "hi"}, AUTH)
    assert_safe_error(caught.value)
    assert implementation.calls == []


@pytest.mark.parametrize("typed", [False, True])
def test_cost_exceptions_are_safe_and_preflight(typed):
    client, implementation, _ = setup_client()

    def fail(request):
        if typed:
            raise ValidationError(TOKEN, issues=[ValidationIssue(path="$", message=TOKEN)])
        raise RuntimeError(TOKEN)

    implementation.estimate_cost_cents = fail
    with pytest.raises(ConnectorError) as caught:
        client.estimate_cost("fixture", "fixture.send", {"message": "hi"}, AUTH)
    assert_safe_error(caught.value)
    assert implementation.calls == []


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
@pytest.mark.parametrize("phase", ["schema", "lazy"])
def test_schema_and_lazy_errors_use_preflight_boundary(monkeypatch, entry, phase):
    client, implementation, _ = setup_client()

    def fail(*args, **kwargs):
        raise RuntimeError(TOKEN)

    if phase == "schema":
        monkeypatch.setattr("stackos_connectors.registry.schema_issues", fail)
    else:
        monkeypatch.setattr(client.registry, "implementation", fail)
    with pytest.raises(ConnectorError) as caught:
        getattr(client, entry)(
            "fixture",
            "fixture.send",
            {"message": "hi"},
            {"method": "api_key", "fields": {"token": TOKEN}},
        )
    assert_safe_error(caught.value)
    assert implementation.calls == []


@pytest.mark.asyncio
async def test_execute_validation_error_is_preflight():
    client, implementation, _ = setup_client()

    def fail(request):
        raise RuntimeError(TOKEN)

    implementation.validate = fail
    with pytest.raises(ConnectorError) as caught:
        await client.execute("fixture", "fixture.send", {"message": "hi"}, AUTH)
    assert_safe_error(caught.value)
    assert implementation.calls == []


@pytest.mark.parametrize("entry", ["validate", "estimate_cost"])
def test_invalid_auth_is_a_safe_validation_failure(entry):
    client, implementation, _ = setup_client()
    if entry == "validate":
        assert client.validate("fixture", "fixture.send", {"message": "hi"}, TOKEN)
    else:
        with pytest.raises(ValidationError) as caught:
            client.estimate_cost("fixture", "fixture.send", {"message": "hi"}, TOKEN)
        assert_safe_error(caught.value)
    assert implementation.calls == []
