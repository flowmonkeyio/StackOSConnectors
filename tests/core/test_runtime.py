import inspect
import sys

import httpx
import pytest
from test_contract import setup_client

from stackos_connectors import (
    CallOptions,
    ConnectorAuth,
    ConnectorError,
    ConnectorFile,
    ConnectorResult,
)
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.shared.base import BaseIntegration
from stackos_connectors.shared.rate_limit import TokenBucket


class CounterLimiter:
    def __init__(self):
        self.count = 0

    async def acquire(self, n=1):
        self.count += n


@pytest.mark.asyncio
async def test_existing_retry_policy_with_caller_limiter(monkeypatch):
    sleeps = []

    async def no_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("stackos_connectors.shared.base.asyncio.sleep", no_sleep)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(503 if len(attempts) < 3 else 200, json={"ok": True})

    limiter = CounterLimiter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        integration = BaseIntegration(payload=b"secret", http=http, rate_limiter=limiter)
        result = await integration.call(op="read", method="GET", url="https://fixture.invalid")
    assert result.data == {"ok": True}
    assert len(attempts) == limiter.count == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_type",
    [(429, RateLimitedError), (403, IntegrationDownError), (503, IntegrationDownError)],
)
async def test_mutation_zero_retry_preserves_error_status(status, error_type):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"code": "provider-code"}, headers={"retry-after": "7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        integration = BaseIntegration(payload=b"secret", http=http)
        with pytest.raises(error_type) as caught:
            await integration.call(op="write", url="https://fixture.invalid", max_retries=0)
    assert len(calls) == 1
    assert caught.value.data["status"] == status
    assert caught.value.data["provider_error"] == {"code": "provider-code"}
    if status == 429:
        assert caught.value.data["retry_after"] == 7


@pytest.mark.asyncio
async def test_rate_limit_survives_public_error_boundary():
    client, implementation, _ = setup_client()

    async def fail(request):
        raise RateLimitedError(
            "slow down", data={"status": 429, "retry_after": 12, "provider_error": {"code": "rate"}}
        )

    implementation.execute = fail
    with pytest.raises(ConnectorError) as caught:
        await client.execute(
            "fixture",
            "fixture.send",
            {"message": "hi"},
            ConnectorAuth("api_key", {"token": "SECRET"}),
        )
    assert caught.value.provider_status_code == 429
    assert caught.value.metadata_json["retry_after"] == 12
    assert caught.value.provider_error == {"code": "rate"}


@pytest.mark.asyncio
async def test_post_send_progress_failure_is_unknown_without_replay():
    client, implementation, _ = setup_client()
    sent = []

    def callback(payload):
        raise OSError("SYNTHETIC_SECRET")

    async def send(request):
        sent.append("message-1")
        request.options.progress_callback({"sent": sent})

    implementation.execute = send
    with pytest.raises(ConnectorError) as caught:
        await client.execute(
            "fixture",
            "fixture.send",
            {"message": "hi"},
            ConnectorAuth("api_key", {"token": "SYNTHETIC_SECRET"}),
            CallOptions(progress_callback=callback),
        )
    assert sent == ["message-1"]
    assert caught.value.metadata_json == {"outcome": "unknown", "retry_safe": False}
    assert "SYNTHETIC_SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_file_progress_echo_redacted_config_preserved():
    client, implementation, _ = setup_client()
    progress = []

    async def send(request):
        request.options.progress_callback(
            {"echo": "SYNTHETIC_SECRET", "url": request.auth.config["url"]}
        )
        return ConnectorResult(
            files=[ConnectorFile(path="/tmp/SYNTHETIC_SECRET", name="SYNTHETIC_SECRET")],
            output_json={"region": request.auth.config["region"]},
        )

    implementation.execute = send
    result = await client.execute(
        "fixture",
        "fixture.send",
        {"message": "hi"},
        ConnectorAuth(
            "api_key",
            {"token": "SYNTHETIC_SECRET"},
            {"url": "https://api.example", "region": "us-east-1"},
        ),
        CallOptions(progress_callback=progress.append),
    )
    assert progress == [{"echo": "[redacted]", "url": "https://api.example"}]
    assert result.files[0].path == "/tmp/[redacted]"
    assert result.files[0].name == "[redacted]"
    assert result.output_json["region"] == "us-east-1"


def test_neutral_import_and_constructor_boundaries():
    assert not any(name == "stackos" or name.startswith("stackos.") for name in sys.modules)
    assert not {"project_id", "run_id", "budget_repo", "run_step_call_repo"} & set(
        inspect.signature(BaseIntegration).parameters
    )
    bucket = TokenBucket.for_qps(2)
    assert bucket.try_acquire(2)
    assert not bucket.try_acquire(1, now=bucket.last)
