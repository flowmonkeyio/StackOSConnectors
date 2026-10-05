"""Terminal generation facts are independent of HTTP submission acknowledgements."""

import importlib

import httpx
import pytest
from test_named_media import CASES, POLL, RAW, URL, NoWait, auth, get_client

from stackos_connectors import CallOptions
from stackos_connectors.errors import ConnectorError

XAI_VIDEO = (
    "xai-imagine",
    "xai_imagine",
    "xai.video.generate",
    {
        "prompt": "scene",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "model": "grok-imagine-video",
        **POLL,
    },
    "https://api.x.ai/v1/videos/generations",
    None,
    {"request_id": "xai-job"},
    ("https://api.x.ai/v1/videos/xai-job", {"status": "done", "video": {"url": URL}}),
)
ASYNC_CASES = [case for case in CASES if case[7]] + [XAI_VIDEO]


@pytest.fixture
def no_wait(monkeypatch):
    import stackos_connectors.shared.base as base

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(base.asyncio, "sleep", no_sleep)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ASYNC_CASES, ids=lambda case: case[0])
@pytest.mark.parametrize(
    "failure", ["missing_receipt", "poll_timeout", "poll_404", "poll_unreadable"]
)
async def test_unresolved_submission_or_poll_remains_unknown(
    case, failure, tmp_path, no_wait, monkeypatch
):
    requests = []
    module = importlib.import_module(
        "stackos_connectors.shared.byteplus.ark"
        if case[0] == "byteplus-seedance"
        else f"stackos_connectors.connectors.{case[1]}.integration"
    )
    ticks = iter([0, 0, 100])
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks))

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={} if failure == "missing_receipt" else case[6],
                headers={"x-request-id": "submission-receipt"},
            )
        if failure == "poll_timeout":
            raise httpx.ReadTimeout("synthetic poll timeout", request=request)
        if failure == "poll_404":
            return httpx.Response(404, json={"error": "job not found"})
        assert failure == "poll_unreadable"
        return httpx.Response(200, content=b"unreadable status")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    metadata = error.value.metadata_json
    assert sum(request.method == "POST" for request in requests) == 1
    assert metadata["provider_executed"] is True
    assert metadata["submission_http_succeeded"] is True
    assert metadata["outcome_unknown"] is True
    assert metadata["retry_safe"] is False
    assert metadata["provider_request_id"] == "submission-receipt"
    if failure != "missing_receipt":
        assert any(
            value in str(metadata)
            for value in ["veo-123", "wan-123", "seedance-123", "kling-123", "xai-job"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ASYNC_CASES, ids=lambda case: case[0])
async def test_explicit_terminal_failure_is_known(case, tmp_path, no_wait):
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json=case[6])
        body = {
            "google-veo": {
                "name": "operations/veo-123",
                "done": True,
                "error": {"message": "rejected"},
            },
            "alibaba-wan": {"output": {"task_id": "wan-123", "task_status": "FAILED"}},
            "byteplus-seedance": {"id": "seedance-123", "status": "failed"},
            "kling-video": {"code": 0, "data": {"task_id": "kling-123", "task_status": "failed"}},
            "xai-imagine": {"request_id": "xai-job", "status": "failed"},
        }[case[0]]
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert error.value.metadata_json["generation_outcome"] == "failed"
    assert error.value.metadata_json["outcome_unknown"] is False
    assert error.value.metadata_json["retry_safe"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [*CASES, XAI_VIDEO], ids=lambda case: case[0])
async def test_known_generation_followed_by_local_write_failure(case, tmp_path, monkeypatch):
    import stackos_connectors.shared.media as media

    def fail(*_args):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(media.os, "replace", fail)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=case[6], headers={"x-request-id": "known-result"})
        if case[7] and str(request.url) == case[7][0]:
            return httpx.Response(200, json=case[7][1])
        return httpx.Response(200, content=RAW, headers={"content-type": "video/mp4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert error.value.metadata_json["generation_outcome"] == "succeeded"
    assert error.value.metadata_json["outcome_unknown"] is False
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["provider_request_id"] == "known-result"
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [CASES[5], CASES[9]], ids=lambda case: case[0])
async def test_known_generation_followed_by_download_failure(case, tmp_path, no_wait):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=case[6])
        if case[7] and str(request.url) == case[7][0]:
            return httpx.Response(200, json=case[7][1])
        raise httpx.ReadTimeout("synthetic download timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert error.value.metadata_json["generation_outcome"] == "succeeded"
    assert error.value.metadata_json["outcome_unknown"] is False
    assert error.value.metadata_json["retry_safe"] is False
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_submission_http_rejection_has_known_outcome(case, tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"message": "invalid request"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert len(requests) == 1
    assert error.value.metadata_json["submission_http_succeeded"] is False
    assert error.value.metadata_json["outcome_unknown"] is False


@pytest.mark.asyncio
async def test_kling_explicit_service_submission_rejection_is_terminal(tmp_path):
    case = CASES[8]

    def handle(request):
        return httpx.Response(200, json={"code": 1001, "message": "rejected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert error.value.metadata_json["submission_http_succeeded"] is True
    assert error.value.metadata_json["generation_outcome"] == "failed"
    assert error.value.metadata_json["outcome_unknown"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [CASES[i] for i in [0, 1, 2, 3, 4]], ids=lambda case: case[0])
async def test_unreadable_sync_generation_response_is_unknown(case, tmp_path):
    bodies = {
        "openai-images": {"data": [{"b64_json": "malformed!"}]},
        "google-gemini-image": {
            "candidates": [{"content": {"parts": [{"inlineData": {"data": "malformed!"}}]}}]
        },
        "reve": {"image": "malformed!"},
        "xai-imagine": {"data": [{"b64_json": "malformed!"}]},
        "byteplus-seedream": {"data": [{"b64_json": "malformed!"}]},
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=bodies[case[0]]))
    ) as http:
        with pytest.raises(ConnectorError) as error:
            await get_client(case).execute(
                case[0],
                case[2],
                case[3],
                auth(case),
                CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
            )
    assert error.value.metadata_json["submission_http_succeeded"] is True
    assert error.value.metadata_json["generation_outcome"] is None
    assert error.value.metadata_json["outcome_unknown"] is True
