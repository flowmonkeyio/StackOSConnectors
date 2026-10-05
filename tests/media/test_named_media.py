"""Public named calls against source-derived provider wire fixtures; no live network."""

import base64
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.errors import ConnectorError

RAW = b"\x89PNG\r\n\x1a\nsynthetic-output"
B64 = base64.b64encode(RAW).decode()
URL = "https://media.example.test/output.mp4"
POLL = {"poll_interval_seconds": 0.001, "poll_timeout_seconds": 60}
TOKEN = "synthetic-media-key"

CASES = [
    (
        "openai-images",
        "openai_images",
        "image.generate",
        {
            "prompt": "scene",
            "model": "gpt-image-2",
            "size": "1536x1024",
            "quality": "medium",
            "n": 1,
            "output_format": "png",
        },
        "https://api.openai.com/v1/images/generations",
        {
            "prompt": "scene",
            "model": "gpt-image-2",
            "size": "1536x1024",
            "quality": "medium",
            "n": 1,
            "output_format": "png",
        },
        {"data": [{"b64_json": B64}]},
        None,
    ),
    (
        "google-gemini-image",
        "google_gemini_image",
        "google.image.generate",
        {
            "prompt": "scene",
            "model": "gemini-3.1-flash-image",
            "aspect_ratio": "1:1",
            "image_size": "1K",
        },
        "https://generativelanguage.googleapis.com/v1/models/gemini-3.1-flash-image:generateContent",
        {
            "contents": [{"role": "user", "parts": [{"text": "scene"}]}],
            "generationConfig": {
                "responseModalities": ["Image"],
                "responseFormat": {"image": {"aspectRatio": "1:1", "imageSize": "1K"}},
            },
        },
        {
            "responseId": "gemini-123",
            "candidates": [
                {"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": B64}}]}}
            ],
        },
        None,
    ),
    (
        "reve",
        "reve",
        "reve.image.generate",
        {"prompt": "scene", "aspect_ratio": "3:2", "version": "latest", "test_time_scaling": 1},
        "https://api.reve.com/v1/image/create",
        {"prompt": "scene", "aspect_ratio": "3:2", "version": "latest", "test_time_scaling": 1},
        {"image": B64, "request_id": "reve-123", "version": "latest", "credits_used": 18},
        None,
    ),
    (
        "xai-imagine",
        "xai_imagine",
        "xai.image.generate",
        {
            "prompt": "scene",
            "model": "grok-imagine-image-quality",
            "aspect_ratio": "auto",
            "resolution": "1k",
            "n": 1,
        },
        "https://api.x.ai/v1/images/generations",
        {
            "prompt": "scene",
            "model": "grok-imagine-image-quality",
            "aspect_ratio": "auto",
            "resolution": "1k",
            "n": 1,
            "response_format": "b64_json",
        },
        {"data": [{"b64_json": B64}], "usage": {"cost_in_usd_ticks": 500000000}},
        None,
    ),
    (
        "byteplus-seedream",
        "byteplus_seedream",
        "byteplus.image.generate",
        {
            "prompt": "scene",
            "model": "seedream-5-0-lite-260128",
            "size": "2K",
            "region": "ap-southeast-1",
            "sequential_image_generation": "disabled",
        },
        "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        {
            "prompt": "scene",
            "model": "seedream-5-0-lite-260128",
            "size": "2K",
            "response_format": "url",
            "sequential_image_generation": "disabled",
        },
        {"data": [{"b64_json": B64}], "usage": {"generated_images": 1}},
        None,
    ),
    (
        "google-veo",
        "google_veo",
        "google.video.generate",
        {
            "prompt": "scene",
            "model": "veo-3.1-generate-preview",
            "mode": "text-to-video",
            "aspect_ratio": "16:9",
            "duration_seconds": 6,
            "resolution": "720p",
            **POLL,
        },
        "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning",
        {
            "instances": [{"prompt": "scene"}],
            "parameters": {"aspectRatio": "16:9", "durationSeconds": 6, "resolution": "720p"},
        },
        {"name": "operations/veo-123"},
        (
            "https://generativelanguage.googleapis.com/v1beta/operations/veo-123",
            {
                "name": "operations/veo-123",
                "done": True,
                "response": {
                    "generateVideoResponse": {"generatedSamples": [{"video": {"uri": URL}}]}
                },
            },
        ),
    ),
    (
        "alibaba-wan",
        "alibaba_wan",
        "alibaba.video.generate",
        {
            "prompt": "scene",
            "mode": "text-to-video",
            "region": "singapore",
            "resolution": "720P",
            "aspect_ratio": "9:16",
            "duration": 5,
            "prompt_extend": False,
            **POLL,
        },
        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        {
            "model": "wan2.6-t2v",
            "input": {"prompt": "scene"},
            "parameters": {"duration": 5, "prompt_extend": False, "size": "720*1280"},
        },
        {"output": {"task_id": "wan-123", "task_status": "PENDING"}, "request_id": "wan-request"},
        (
            "https://dashscope-intl.aliyuncs.com/api/v1/tasks/wan-123",
            {"output": {"task_id": "wan-123", "task_status": "SUCCEEDED", "video_url": URL}},
        ),
    ),
    (
        "byteplus-seedance",
        "byteplus_seedance",
        "byteplus.video.generate",
        {
            "prompt": "scene",
            "model": "dreamina-seedance-2-0-260128",
            "mode": "text-to-video",
            "region": "ap-southeast-1",
            "resolution": "720p",
            "ratio": "16:9",
            "duration": 5,
            **POLL,
        },
        "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks",
        {
            "model": "dreamina-seedance-2-0-260128",
            "content": [{"type": "text", "text": "scene"}],
            "resolution": "720p",
            "ratio": "16:9",
            "duration": 5,
        },
        {"id": "seedance-123"},
        (
            "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/seedance-123",
            {"id": "seedance-123", "status": "succeeded", "content": {"video_url": URL}},
        ),
    ),
    (
        "kling-video",
        "kling_video",
        "kling.video.generate",
        {
            "prompt": "scene",
            "mode": "text-to-video",
            "model_name": "kling-v3",
            "quality_mode": "pro",
            "duration": 5,
            "aspect_ratio": "1:1",
            "sound": "off",
            **POLL,
        },
        "https://api-singapore.klingai.com/v1/videos/text2video",
        {
            "model_name": "kling-v3",
            "prompt": "scene",
            "duration": "5",
            "mode": "pro",
            "sound": "off",
            "aspect_ratio": "1:1",
        },
        {
            "code": 0,
            "request_id": "kling-request",
            "data": {"task_id": "kling-123", "task_status": "submitted"},
        },
        (
            "https://api-singapore.klingai.com/v1/videos/text2video/kling-123",
            {
                "code": 0,
                "data": {
                    "task_id": "kling-123",
                    "task_status": "succeed",
                    "task_result": {"videos": [{"id": "video-1", "url": URL}]},
                },
            },
        ),
    ),
    (
        "ideogram",
        "ideogram",
        "ideogram.image.generate",
        {"text_prompt": "scene", "rendering_speed": "DEFAULT"},
        "https://api.ideogram.ai/v1/ideogram-v4/generate",
        None,
        {"data": [{"url": URL}], "request_id": "ideo-123"},
        None,
    ),
]


class NoWait:
    async def acquire(self, n=1):
        pass


def get_client(case):
    return ConnectorClient(registry=load_registry(f"connectors/{case[1]}/catalog.json"))


def auth(case):
    if case[0] == "kling-video":
        return ConnectorAuth(
            "access_key_secret", {"access_key": "synthetic-access", "secret_key": TOKEN}
        )
    return ConnectorAuth("api_key", {"api_key": TOKEN})


def assert_auth(case, request):
    if case[0] == "kling-video":
        encoded = request.headers["authorization"].removeprefix("Bearer ")
        header, payload, signature = encoded.split(".")
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(TOKEN.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        assert signature == expected
        assert (
            json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["iss"]
            == "synthetic-access"
        )
    elif case[0].startswith("google-"):
        assert request.headers["x-goog-api-key"] == TOKEN
    elif case[0] == "ideogram":
        assert request.headers["api-key"] == TOKEN
    else:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
    if case[0] == "alibaba-wan" and request.method == "POST":
        assert request.headers["x-dashscope-async"] == "enable"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_named_generation_wire_and_files(case, tmp_path):
    provider, _, action, data, url, expected, response, poll = case
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            assert str(request.url) == url
            assert_auth(case, request)
            if expected is not None:
                assert json.loads(request.content) == expected
            else:
                assert b'name="text_prompt"\r\n\r\nscene\r\n' in request.content
                assert b'name="rendering_speed"\r\n\r\nDEFAULT\r\n' in request.content
            return httpx.Response(200, json=response, headers={"x-request-id": "submit-id"})
        if poll and str(request.url) == poll[0]:
            assert_auth(case, request)
            return httpx.Response(200, json=poll[1])
        assert str(request.url) == URL
        assert "authorization" not in request.headers
        assert "x-goog-api-key" not in request.headers
        return httpx.Response(200, content=RAW, headers={"content-type": "video/mp4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await get_client(case).execute(
            provider,
            action,
            data,
            auth(case),
            CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
        )
    assert sum(request.method == "POST" for request in requests) == 1
    assert len(result.files) == 1
    assert Path(result.files[0].path).read_bytes() == RAW
    assert result.output_json["data"][0]["path"] == result.files[0].path
    assert result.metadata_json["provider_request_id"] == "submit-id"
    assert "generated-assets" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
@pytest.mark.parametrize("failure", ["transport", "503"])
async def test_generation_never_resubmits(case, tmp_path, failure):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(
            503,
            json={"error": {"message": "unavailable"}},
            headers={"x-request-id": "error-receipt"},
        )

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
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["outcome_unknown"] is True
    if failure == "503":
        assert error.value.metadata_json["provider_request_id"] == "error-receipt"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_file_failure_retains_receipts_without_resubmission(case, tmp_path, monkeypatch):
    import stackos_connectors.shared.media as media

    def fail(*args):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(media.os, "replace", fail)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, json=case[6], headers={"x-request-id": "accepted-generation"}
            )
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
    assert sum(request.method == "POST" for request in requests) == 1
    assert error.value.metadata_json["provider_executed"] is True
    assert error.value.metadata_json["submission_http_succeeded"] is True
    assert error.value.metadata_json["provider_request_id"] == "accepted-generation"
    assert error.value.metadata_json["retry_safe"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_safe_poll_retry_preserved(tmp_path, monkeypatch):
    import stackos_connectors.shared.base as base

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(base.asyncio, "sleep", no_sleep)
    case = CASES[5]
    requests = []
    polls = 0

    def handle(request):
        nonlocal polls
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=case[6])
        if str(request.url) == case[7][0]:
            polls += 1
            return (
                httpx.Response(503, json={"error": "unavailable"})
                if polls == 1
                else httpx.Response(200, json=case[7][1])
            )
        return httpx.Response(200, content=RAW, headers={"content-type": "video/mp4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await get_client(case).execute(
            case[0],
            case[2],
            case[3],
            auth(case),
            CallOptions(http=http, output_dir=tmp_path, rate_limiter=NoWait()),
        )
    assert sum(request.method == "POST" for request in requests) == 1
    assert polls == 2
    assert len(result.files) == 1


def test_every_media_submission_explicitly_disables_retries():
    import ast
    import importlib
    import inspect

    sources = {
        f"stackos_connectors.connectors.{case[1]}.integration"
        for case in CASES
        if not case[0].startswith("byteplus-")
    }
    sources.update(
        {
            "stackos_connectors.shared.byteplus.ark",
            "stackos_connectors.connectors.aignc.integration",
        }
    )
    for module_name in sources:
        tree = ast.parse(inspect.getsource(importlib.import_module(module_name)))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "call"
            ):
                continue
            keywords = {item.arg: item.value for item in node.keywords}
            method = keywords.get("method", ast.Constant("POST"))
            if isinstance(method, ast.Constant) and method.value == "GET":
                continue
            assert isinstance(keywords.get("max_retries"), ast.Constant), module_name
            assert keywords["max_retries"].value == 0, module_name
