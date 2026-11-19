import base64
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared import media

DESIGN = "elevenlabs.voices.design"
CREATE = "elevenlabs.voices.create"
KEY = "synthetic-voice-design-key"
AUTH = ConnectorAuth(method="api_key", fields={"api_key": KEY})
DESCRIPTION = "A calm and clear voice with a warm tone."
TEXT = "The quiet studio is ready for its first recording. " * 3
DESIGN_INPUT = {"voice_description": DESCRIPTION, "auto_generate_text": True}
CREATE_INPUT = {
    "generated_voice_id": "independent-caller-selection",
    "voice_name": "Warm narrator",
    "voice_description": DESCRIPTION,
}


async def execute(action, data, handle, *, output_dir=None):
    client = get_default_client()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        result = await client.execute(
            "elevenlabs", action, data, AUTH, CallOptions(http=http, output_dir=output_dir)
        )
        assert not http.is_closed
    schema = client.describe("elevenlabs", action)["actions"][0]["output_schema"]
    Draft202012Validator(schema).validate(result.output_json)
    return result


def preview(voice_id, duration, mime, audio):
    return {
        "generated_voice_id": voice_id,
        "duration_secs": duration,
        "media_type": mime,
        "audio_base_64": base64.b64encode(audio).decode("ascii"),
    }


def assert_post(request, path):
    assert request.method == "POST"
    assert request.url.copy_with(query=None) == f"https://api.elevenlabs.io{path}"
    assert request.headers["xi-api-key"] == KEY
    assert request.headers["content-type"] == "application/json"


@pytest.mark.parametrize("action,risk", [(DESIGN, "cost"), (CREATE, "write")])
def test_voice_design_catalog_exposes_authenticated_post_actions(action, risk):
    description = get_default_client().describe("elevenlabs", action)["actions"][0]
    assert description["risk_level"] == risk
    assert description["read_only"] is False
    assert description["auth_optional"] is False


@pytest.mark.parametrize("automatic", [False, True])
async def test_design_sends_one_post_and_persists_each_preview(tmp_path, automatic):
    body = {
        **DESIGN_INPUT,
        "model_id": "eleven_ttv_v3",
        "text": None if automatic else TEXT,
        "auto_generate_text": automatic,
        "seed": 0,
        "loudness": 0,
        "guidance_scale": 0,
        "should_enhance": False,
        "quality": None,
        "reference_audio_base64": "AQID",
        "prompt_strength": 0,
        "stream_previews": False,
    }
    audio = [b"synthetic mp3", b"synthetic wav", b"synthetic mp3"]
    previews = [
        preview("preview-1", 1.125, "audio/mpeg", audio[0]),
        {**preview("preview-2", 2.25, "audio/x-wav", audio[1]), "language": None},
        preview("preview-3", 3, "audio/mpeg", audio[2]),
    ]
    seen = []

    def handle(request):
        seen.append(request)
        assert_post(request, "/v1/text-to-voice/design")
        assert dict(request.url.params) == {"output_format": "mp3_22050_32"}
        assert json.loads(request.content) == body
        return httpx.Response(
            200, json={"text": TEXT, "previews": previews, "voice_id": "unsolicited"}
        )

    result = await execute(
        DESIGN, {**body, "output_format": "mp3_22050_32"}, handle, output_dir=tmp_path
    )
    assert len(seen) == 1
    assert result.output_json == {
        "status": "success",
        "text": TEXT,
        "previews": [
            {**{k: v for k, v in row.items() if k != "audio_base_64"}, "file_index": index}
            for index, row in enumerate(previews)
        ],
    }
    paths = [Path(file.path) for file in result.files]
    assert len(set(paths)) == len(result.files) == 3
    for index, (row, extension) in enumerate(zip(previews, (".mp3", ".wav", ".mp3"), strict=True)):
        file_index = result.output_json["previews"][index]["file_index"]
        file = result.files[file_index]
        path = Path(file.path)
        assert path.parent == tmp_path.resolve()
        assert path.suffix == extension
        assert path.read_bytes() == audio[index]
        assert file.mime_type == row["media_type"]
        assert file.size_bytes == len(audio[index])
    assert set(tmp_path.iterdir()) == set(paths)


@pytest.mark.parametrize(
    ("middle_type", "middle_suffix"),
    [
        ("application/octet-stream", ".bin"),
        ("audio/L16;\trate=16000", ".pcm"),
        ('application/vnd.example+binary; label="take; \\"one\\""', ".bin"),
    ],
)
async def test_design_saves_all_previews_with_mixed_media_types(
    tmp_path, middle_type, middle_suffix
):
    audio = b"\x00\x00\x38\x01" * 24
    media_types = ["audio/pcm", middle_type, "audio/pcm"]
    previews = [
        preview(f"preview-{index}", index + 0.5, media_type, audio)
        for index, media_type in enumerate(media_types)
    ]
    seen = []

    def handle(request):
        seen.append(request)
        assert_post(request, "/v1/text-to-voice/design")
        return httpx.Response(200, json={"text": TEXT, "previews": previews})

    result = await execute(DESIGN, DESIGN_INPUT, handle, output_dir=tmp_path)
    assert len(seen) == 1
    assert result.output_json["status"] == "success"
    paths = []
    for index, (row, suffix) in enumerate(
        zip(result.output_json["previews"], [".pcm", middle_suffix, ".pcm"], strict=True)
    ):
        assert row == {
            "generated_voice_id": f"preview-{index}",
            "duration_secs": index + 0.5,
            "media_type": media_types[index],
            "file_index": index,
        }
        file = result.files[row["file_index"]]
        assert file.mime_type == media_types[index]
        path = Path(file.path)
        assert path.suffix == suffix
        assert path.read_bytes() == audio
        paths.append(path)
    assert len(result.files) == len(set(paths)) == 3


@pytest.mark.parametrize(
    "middle_type",
    ["", " \t", "audio/", "audio/*", 'audio/ogg; note="unfinished', "audio/ogg;\r\nrate=16000"],
)
async def test_design_keeps_valid_previews_when_one_media_type_is_malformed(tmp_path, middle_type):
    audio = [b"first preview", b"middle preview", b"last preview"]
    previews = [
        preview("first", 1, "audio/mpeg", audio[0]),
        preview("middle", 2.5, middle_type, audio[1]),
        preview("last", 3, "audio/ogg", audio[2]),
    ]
    seen = []

    def handle(request):
        seen.append(request)
        assert_post(request, "/v1/text-to-voice/design")
        return httpx.Response(200, json={"text": TEXT, "previews": previews})

    result = await execute(DESIGN, DESIGN_INPUT, handle, output_dir=tmp_path)
    assert len(seen) == 1
    assert result.output_json == {
        "status": "partial",
        "text": TEXT,
        "previews": [
            {
                "generated_voice_id": "first",
                "duration_secs": 1,
                "media_type": "audio/mpeg",
                "file_index": 0,
            },
            {
                "generated_voice_id": "middle",
                "duration_secs": 2.5,
                "reason_code": "invalid_preview",
            },
            {
                "generated_voice_id": "last",
                "duration_secs": 3,
                "media_type": "audio/ogg",
                "file_index": 1,
            },
        ],
    }
    assert [file.mime_type for file in result.files] == ["audio/mpeg", "audio/ogg"]
    assert [Path(file.path).read_bytes() for file in result.files] == [audio[0], audio[2]]
    assert set(tmp_path.iterdir()) == {Path(file.path) for file in result.files}


@pytest.mark.parametrize("failure", ["write", "malformed_audio", "all_writes"])
async def test_design_outcome_preserves_ids_and_indexes_after_preview_failure(
    tmp_path, monkeypatch, failure
):
    audio = [b"first preview", b"second preview", b"third preview"]
    rows = [
        preview(f"preview-{index}", index, "audio/mpeg", chunk) for index, chunk in enumerate(audio)
    ]
    if failure == "malformed_audio":
        rows[1]["audio_base_64"] = "!invalid!"
    replace = media.os.replace
    seen = []
    writes = []

    def replace_with_failure(source, target):
        writes.append(target)
        if failure == "all_writes" or (failure == "write" and len(writes) == 2):
            raise OSError("private disk diagnostic")
        replace(source, target)

    def handle(request):
        seen.append(request)
        assert_post(request, "/v1/text-to-voice/design")
        assert json.loads(request.content) == DESIGN_INPUT
        monkeypatch.setattr(media.os, "replace", replace_with_failure)
        return httpx.Response(
            200,
            json={"text": TEXT, "previews": rows},
            headers={"request-id": "design-receipt"},
        )

    result = await execute(DESIGN, DESIGN_INPUT, handle, output_dir=tmp_path)
    all_failed = failure == "all_writes"
    assert len(seen) == 1
    assert result.output_json["status"] == ("failed" if all_failed else "partial")
    results = result.output_json["previews"]
    assert [row["generated_voice_id"] for row in results] == [f"preview-{i}" for i in range(3)]
    assert [row["duration_secs"] for row in results] == [0, 1, 2]
    assert [row.get("file_index") for row in results] == (
        [None, None, None] if all_failed else [0, None, 1]
    )
    assert results[1]["reason_code"] == (
        "invalid_preview" if failure == "malformed_audio" else "audio_persistence_failed"
    )
    assert [Path(file.path).read_bytes() for file in result.files] == (
        [] if all_failed else [audio[0], audio[2]]
    )
    assert set(tmp_path.iterdir()) == {Path(file.path) for file in result.files}
    metadata = result.metadata_json
    assert metadata["provider_request_id"] == "design-receipt"
    assert metadata["provider_executed"] is True
    assert metadata["outcome_unknown"] is False
    assert metadata["charge_possible"] is True
    assert metadata["retry_safe"] is False
    assert metadata["automatic_retry_count"] == 0
    assert "private disk diagnostic" not in repr(metadata) + repr(result.output_json)


async def test_create_sends_caller_selected_id_in_one_standalone_post():
    seen = []

    def handle(request):
        seen.append(request)
        assert_post(request, "/v1/text-to-voice")
        assert not request.url.params
        assert json.loads(request.content) == CREATE_INPUT
        return httpx.Response(200, json={"voice_id": "permanent-id", "name": "Warm narrator"})

    result = await execute(CREATE, CREATE_INPUT, handle)
    assert len(seen) == 1
    assert result.output_json == {"voice_id": "permanent-id"}
    assert not result.files


async def test_create_requires_a_caller_selected_preview():
    with pytest.raises(ValidationError):
        await execute(
            CREATE,
            {key: value for key, value in CREATE_INPUT.items() if key != "generated_voice_id"},
            lambda _: pytest.fail("unexpected POST"),
        )


@pytest.mark.parametrize("action,body", [(DESIGN, DESIGN_INPUT), (CREATE, CREATE_INPUT)])
@pytest.mark.parametrize("status", [None, 409, 429, 503, 307, 200])
async def test_design_and_create_do_not_repeat_after_provider_failure(
    tmp_path, action, body, status
):
    seen = []

    def handle(request):
        seen.append(request)
        assert_post(
            request, "/v1/text-to-voice/design" if action == DESIGN else "/v1/text-to-voice"
        )
        if status is None:
            raise httpx.ReadTimeout("ambiguous timeout", request=request)
        return httpx.Response(
            status, headers={"Location": "https://redirect.test/", "request-id": "post-receipt"}
        )

    with pytest.raises(ConnectorError) as error:
        await execute(action, body, handle, output_dir=tmp_path)
    assert len(seen) == 1
    assert error.value.provider_status_code == status
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["automatic_retry_count"] == 0
    assert error.value.metadata_json.get("provider_request_id") == (
        "post-receipt" if status is not None else None
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "data",
    [
        {"voice_description": DESCRIPTION},
        {**DESIGN_INPUT, "text": TEXT},
        {**DESIGN_INPUT, "text": "too short", "auto_generate_text": False},
        {**DESIGN_INPUT, "model_id": "eleven_multilingual_v2"},
        {**DESIGN_INPUT, "prompt_strength": 0},
        {**DESIGN_INPUT, "stream_previews": True},
        {**DESIGN_INPUT, "seed": 2**31},
    ],
)
async def test_invalid_voice_design_options_never_dispatch(tmp_path, data):
    with pytest.raises(ValidationError):
        await execute(DESIGN, data, lambda _: pytest.fail("unexpected POST"), output_dir=tmp_path)


@pytest.mark.parametrize("use_existing_file", [False, True])
async def test_design_checks_the_output_directory_before_dispatch(tmp_path, use_existing_file):
    target = None
    if use_existing_file:
        target = tmp_path / "existing"
        target.touch()
    with pytest.raises(ValidationError):
        await execute(
            DESIGN, DESIGN_INPUT, lambda _: pytest.fail("unexpected POST"), output_dir=target
        )
