import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    ConnectorAuth,
    ConnectorClient,
    ConnectorRegistry,
    ValidationError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["validate", "execute"])
async def test_validation_outcome_preserves_executed_read_but_preflight_forces_false(phase):
    secret = "SYNTHETIC-VALIDATION-OUTCOME-KEY"

    class Provider:
        key = "fixture"

        def validate(self, request):
            if phase == "validate":
                self.fail()
            return []

        async def execute(self, request):
            self.fail()

        def fail(self):
            raise ValidationError(
                "paid request rejected",
                data={"limit": 100, "echo": secret},
                metadata_json={
                    "provider_executed": True,
                    "primary_request_executed": False,
                    "retry_safe": True,
                    "diagnostic": secret,
                    "preflight_requests": ["limits_and_usage"],
                },
            )

    definition = ActionDefinition(
        "fixture", "check", "check", auth_methods=(AuthMethodDefinition("key", {"type": "object"}),)
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(actions=[definition], implementations={"fixture": Provider})
    )
    with pytest.raises(ValidationError) as caught:
        await client.execute("fixture", "check", {}, ConnectorAuth("key", {"token": secret}))
    assert caught.value.metadata_json["provider_executed"] is (phase == "execute")
    assert secret not in str(caught.value.metadata_json)
    assert secret not in str(caught.value.data)
    assert caught.value.data["limit"] == 100
    if phase == "execute":
        assert caught.value.metadata_json["primary_request_executed"] is False
        assert caught.value.metadata_json["retry_safe"] is True
        assert caught.value.metadata_json["preflight_requests"] == ["limits_and_usage"]
