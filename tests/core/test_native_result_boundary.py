import asyncio

import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ConnectorFile,
    ConnectorRegistry,
    ConnectorResult,
    ValidationError,
    ValidationIssue,
)

SIGNED_URL = "https://provider.example/file?signature=provider-signature"
CURSOR = "continuation?token=provider-page-token"
AUTH_SECRET = "resolved-auth-canary"


class Fixture:
    key = "facts"

    def validate(self, request):
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        value = {
            "url": SIGNED_URL,
            "cursor": CURSOR,
            "echo": AUTH_SECRET,
            "token": "native-next-token",
        }
        if request.options.progress_callback:
            request.options.progress_callback(value)
        if request.input_json.get("fail"):
            raise ConnectorError("failed", output_json=value, metadata_json=value)
        return ConnectorResult(output_json=value, metadata_json=value)


def client():
    definition = ActionDefinition(
        "facts", "read", "read", auth_methods=(AuthMethodDefinition("key"),)
    )
    return ConnectorClient(
        registry=ConnectorRegistry(actions=[definition], implementations={"facts": Fixture})
    )


def test_native_success_facts_survive_but_auth_echo_and_diagnostics_are_scrubbed():
    progress = []
    result = asyncio.run(
        client().execute(
            "facts",
            "read",
            {},
            ConnectorAuth("key", {"api_key": AUTH_SECRET}),
            CallOptions(progress_callback=progress.append),
        )
    )
    assert result.output_json == {
        "url": SIGNED_URL,
        "cursor": CURSOR,
        "echo": "[redacted]",
        "token": "native-next-token",
    }
    assert result.metadata_json["url"] != SIGNED_URL
    assert result.metadata_json["cursor"] != CURSOR
    assert result.metadata_json["token"] == "[redacted]"
    assert progress[0] == result.metadata_json
    assert AUTH_SECRET not in repr(result)


def test_provider_failures_keep_full_redaction():
    with pytest.raises(ConnectorError) as failure:
        asyncio.run(
            client().execute(
                "facts", "read", {"fail": True}, ConnectorAuth("key", {"api_key": AUTH_SECRET})
            )
        )
    assert failure.value.output_json["url"] != SIGNED_URL
    assert failure.value.output_json["cursor"] != CURSOR
    assert failure.value.output_json["token"] == "[redacted]"
    assert failure.value.output_json["echo"] == "[redacted]"


@pytest.mark.parametrize("secret", ["p", "metadata_json", "cost_cents", "path"])
def test_short_secret_cannot_corrupt_result_or_file_envelope(secret):
    calls = []

    class Submitted(Fixture):
        async def execute(self, request):
            calls.append(request)
            return ConnectorResult(
                output_json={"id": "job-42", "state": "done", "echo": secret},
                metadata_json={
                    "id": "job-42",
                    "state": "done",
                    "echo": secret,
                    "outcome": "known",
                    "retry_safe": False,
                    "provider_executed": True,
                    "primary_request_executed": True,
                    "outcome_unknown": False,
                    "provider_receipt": {"id": "job-42", "echo": secret},
                },
                cost_cents=7,
                files=[ConnectorFile(path=f"/generated/{secret}", name=secret, size_bytes=9)],
            )

    native = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[
                ActionDefinition(
                    "facts", "read", "read", auth_methods=(AuthMethodDefinition("key"),)
                )
            ],
            implementations={"facts": Submitted},
        )
    )
    result = asyncio.run(
        native.execute("facts", "read", {}, ConnectorAuth("key", {"token": secret}))
    )
    assert len(calls) == 1
    assert result.output_json == {"id": "job-42", "state": "done", "echo": "[redacted]"}
    assert result.metadata_json == {
        **result.output_json,
        "outcome": "known",
        "retry_safe": False,
        "provider_executed": True,
        "primary_request_executed": True,
        "outcome_unknown": False,
        "provider_receipt": {"id": "job-42", "echo": "[redacted]"},
    }
    assert result.cost_cents == 7
    assert result.files[0].path == "/generated/[redacted]"
    assert result.files[0].name == "[redacted]"
    assert result.files[0].size_bytes == 9


def test_short_secret_does_not_erase_failure_receipt_controls():
    class Failed(Fixture):
        async def execute(self, request):
            raise ConnectorError(
                "failed",
                metadata_json={
                    "provider_executed": True,
                    "primary_request_executed": True,
                    "retry_safe": False,
                    "outcome_unknown": True,
                    "provider_receipt": {"id": "job-42", "echo": "p"},
                },
            )

    native = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[
                ActionDefinition(
                    "facts", "read", "read", auth_methods=(AuthMethodDefinition("key"),)
                )
            ],
            implementations={"facts": Failed},
        )
    )
    with pytest.raises(ConnectorError) as error:
        asyncio.run(native.execute("facts", "read", {}, ConnectorAuth("key", {"token": "p"})))
    assert error.value.metadata_json == {
        "provider_executed": True,
        "primary_request_executed": True,
        "retry_safe": False,
        "outcome_unknown": True,
        "provider_receipt": {"id": "job-42", "echo": "[redacted]"},
    }


@pytest.mark.parametrize("phase", ["validate", "execute"])
def test_secret_matching_validation_field_preserves_diagnostic_envelope(phase):
    class Invalid(Fixture):
        def validate(self, request):
            if phase == "validate":
                return [ValidationIssue(path="$.field", message="echo path", code="invalid")]
            return []

        async def execute(self, request):
            raise ValidationError(
                "invalid",
                issues=[ValidationIssue(path="$.field", message="echo path", code="invalid")],
            )

    native = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[
                ActionDefinition(
                    "facts", "read", "read", auth_methods=(AuthMethodDefinition("key"),)
                )
            ],
            implementations={"facts": Invalid},
        )
    )
    auth = ConnectorAuth("key", {"token": "path"})
    if phase == "validate":
        issues = native.validate("facts", "read", {}, auth)
    else:
        with pytest.raises(ValidationError) as error:
            asyncio.run(native.execute("facts", "read", {}, auth))
        issues = error.value.issues
    assert len(issues) == 1
    assert issues[0].model_dump() == {
        "path": "$.field",
        "message": "echo [redacted]",
        "code": "invalid",
    }
