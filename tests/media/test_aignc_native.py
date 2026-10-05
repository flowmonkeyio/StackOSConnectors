import base64
import json
from pathlib import Path

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.errors import ConnectorError, ValidationError

JPEG = b"\xff\xd8\xffsynthetic\xff\xd9"
AUTH = ConnectorAuth("api_key", {"api_key": "synthetic-aignc-token"})


def client():
    return ConnectorClient(registry=load_registry("connectors/aignc/catalog.json"))


def image_response():
    return {
        "id": "completion-7",
        "model": "gemini-3.1-flash-image",
        "choices": [
            {"finish_reason": "stop", "message": {"content": base64.b64encode(JPEG).decode()}}
        ],
    }


@pytest.mark.asyncio
async def test_named_image_exact_wire_and_plain_file(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=image_response(), headers={"cf-aig-log-id": "request-7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client().execute(
            "aignc",
            "aignc.image.generate",
            {"prompt": "clouds", "max_tokens": 123},
            AUTH,
            CallOptions(http=http, output_dir=tmp_path),
        )
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://cli-api.f2nd.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer synthetic-aignc-token"
    assert json.loads(seen[0].content) == {
        "model": "gemini-3.1-flash-image",
        "messages": [{"role": "user", "content": "clouds"}],
        "max_tokens": 123,
        "stream": False,
    }
    assert len(result.files) == 1
    assert Path(result.files[0].path).read_bytes() == JPEG
    assert result.files[0].mime_type == "image/jpeg"
    assert result.output_json["data"][0]["path"] == result.files[0].path
    assert result.output_json["provider_request_id"] == "request-7"
    assert "generated-assets" not in result.model_dump_json()
    assert result.cost_cents == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,value", [("max_response_bytes", True), ("max_image_bytes", 0), ("max_audio_bytes", -1)]
)
async def test_invalid_caller_limit_has_no_dispatch(tmp_path, limit, value):
    def handle(request):
        pytest.fail("invalid limit dispatched")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValidationError):
            await client().execute(
                "aignc",
                "aignc.image.generate",
                {"prompt": "clouds", "max_tokens": 123},
                AUTH,
                CallOptions(http=http, output_dir=tmp_path, provider_context={limit: value}),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [{"max_image_bytes": 1}, {"max_response_bytes": 1}])
async def test_post_generation_cap_preserves_receipt(tmp_path, context):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=image_response(), headers={"cf-aig-log-id": "request-cap"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await client().execute(
                "aignc",
                "aignc.image.generate",
                {"prompt": "clouds", "max_tokens": 123},
                AUTH,
                CallOptions(http=http, output_dir=tmp_path, provider_context=context),
            )
    assert len(seen) == 1
    assert error.value.metadata_json["provider_executed"] is True
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["provider_request_id"] == "request-cap"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_file_failure_does_not_resubmit(tmp_path, monkeypatch):
    import stackos_connectors.shared.media as media

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(media.os, "replace", fail)
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=image_response(), headers={"cf-aig-log-id": "request-file"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await client().execute(
                "aignc",
                "aignc.image.generate",
                {"prompt": "clouds", "max_tokens": 123},
                AUTH,
                CallOptions(http=http, output_dir=tmp_path),
            )
    assert len(seen) == 1
    assert error.value.metadata_json["id"] == "completion-7"
    assert error.value.metadata_json["provider_request_id"] == "request-file"
    assert error.value.metadata_json["provider_executed"] is True
    assert error.value.metadata_json["retry_safe"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "server"])
async def test_ambiguous_post_never_retries(tmp_path, failure):
    seen = []

    def handle(request):
        seen.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await client().execute(
                "aignc",
                "aignc.image.generate",
                {"prompt": "clouds", "max_tokens": 123},
                AUTH,
                CallOptions(http=http, output_dir=tmp_path),
            )
    assert len(seen) == 1
    assert error.value.metadata_json["outcome_unknown"] is True
    assert error.value.metadata_json["retry_safe"] is False
