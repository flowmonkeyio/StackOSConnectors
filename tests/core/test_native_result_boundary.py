import asyncio

import pytest

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ConnectorRegistry,
    ConnectorResult,
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
