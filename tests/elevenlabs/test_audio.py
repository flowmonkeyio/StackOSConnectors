import base64
import json
import wave
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared import media

SPEECH = "elevenlabs.speech.generate"
ALIGN = "elevenlabs.alignment.create"
KEY = "synthetic-elevenlabs-key"
AUTH = ConnectorAuth(method="api_key", fields={"api_key": KEY})
SPEECH_DATA = {"voice_id": "voice-1", "text": " Café  世界 "}


def wave_bytes():
    data = BytesIO()
    with wave.open(data, "wb") as stream:
        stream.setparams((1, 2, 24_000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00\xaf\x01" * 24)
    return data.getvalue()


AUDIO = wave_bytes()


async def execute(action, data, handle, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await get_default_client().execute(
            "elevenlabs", action, data, AUTH, CallOptions(http=http, **options)
        )
        assert not http.is_closed
    schema = get_default_client().describe("elevenlabs", action)["actions"][0]["output_schema"]
    Draft202012Validator(schema).validate(result.output_json)
    return result


@pytest.mark.parametrize("action", [SPEECH, ALIGN])
def test_audio_catalog_exposes_paid_authenticated_actions(action):
    description = get_default_client().describe("elevenlabs", action)["actions"][0]
    assert description["risk_level"] == "cost"
    assert description["read_only"] is False
    assert description["auth_optional"] is False


@pytest.fixture
def alignment_input(tmp_path):
    source = tmp_path / "standalone.wav"
    source.write_bytes(AUDIO)
    return {"audio_path": str(source), "mime_type": "audio/x-wav", "text": " Café  世界 "}


async def test_speech_sends_one_exact_post_and_preserves_alignments(tmp_path, monkeypatch):
    alignment = {
        "characters": ["é", " ", "世"],
        "character_start_times_seconds": [0, 0.125, 0.75],
        "character_end_times_seconds": [0.125, 0.75, 1.125],
    }
    normalized = {
        "characters": ["É", " "],
        "character_start_times_seconds": [0.0625, 0.75],
        "character_end_times_seconds": [0.5, 1.125],
    }
    body = {
        "text": SPEECH_DATA["text"],
        "model_id": "model-1",
        "voice_settings": {
            "stability": 0,
            "similarity_boost": None,
            "use_speaker_boost": False,
        },
        "previous_text": " Avant ",
        "next_text": "",
        "seed": 0,
        "pronunciation_dictionary_locators": [
            {"pronunciation_dictionary_id": "b", "version_id": "v2"},
            {"pronunciation_dictionary_id": "a"},
            {"pronunciation_dictionary_id": "c", "version_id": None},
        ],
    }
    encoded = base64.b64encode(AUDIO).decode()
    seen = []
    decode_calls = []
    original_decode = media.base64.b64decode

    def decode(value, **kwargs):
        decode_calls.append(value)
        return original_decode(value, **kwargs)

    monkeypatch.setattr(media.base64, "b64decode", decode)

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://api.elevenlabs.io/v1/text-to-speech/voice%2Fone/with-timestamps"
            "?output_format=wav_24000"
        )
        assert request.headers["xi-api-key"] == KEY
        assert request.extensions["timeout"]["read"] == 12.0
        assert json.loads(request.content) == body
        return httpx.Response(
            200,
            json={
                "audio_base64": encoded,
                "alignment": alignment,
                "normalized_alignment": normalized,
            },
            headers={"request-id": "speech-receipt", "history-item-id": "history-1"},
        )

    result = await execute(
        SPEECH,
        {**body, "voice_id": "voice/one", "output_format": "wav_24000"},
        handle,
        output_dir=tmp_path,
        timeout=12.0,
    )
    assert len(seen) == 1
    assert decode_calls == [encoded]
    assert result.output_json == {
        "output_format": "wav_24000",
        "alignment": alignment,
        "normalized_alignment": normalized,
    }
    assert result.metadata_json["provider_request_id"] == "speech-receipt"
    assert result.metadata_json["history_item_id"] == "history-1"
    (file,) = result.files
    path = Path(file.path)
    assert path.parent == tmp_path.resolve()
    assert path.name == file.name
    assert path.suffix == ".wav"
    assert file.mime_type == "audio/wav"
    assert path.read_bytes() == AUDIO
    assert file.size_bytes == len(AUDIO)


@pytest.mark.parametrize(
    "selected,extension,mime",
    [
        (None, ".mp3", "audio/mpeg"),
        ("mp3_44100_192", ".mp3", "audio/mpeg"),
        ("opus_48000_128", ".opus", "audio/ogg"),
        ("pcm_24000", ".pcm", "audio/pcm"),
        ("ulaw_8000", ".ulaw", "audio/basic"),
        ("alaw_8000", ".alaw", "audio/PCMA"),
    ],
)
async def test_speech_formats_and_nullable_alignment(tmp_path, selected, extension, mime):
    def handle(request):
        assert dict(request.url.params) == ({"output_format": selected} if selected else {})
        assert json.loads(request.content) == {"text": SPEECH_DATA["text"]}
        return httpx.Response(
            200, json={"audio_base64": base64.b64encode(AUDIO).decode(), "alignment": None}
        )

    data = {**SPEECH_DATA, **({"output_format": selected} if selected else {})}
    result = await execute(SPEECH, data, handle, output_dir=tmp_path)
    assert result.output_json == {"output_format": selected or "mp3_44100_128", "alignment": None}
    (file,) = result.files
    assert Path(file.path).suffix == extension
    assert file.mime_type == mime


async def test_alignment_uploads_standalone_multipart_and_preserves_result(alignment_input):
    reply = {
        "characters": [{"text": " ", "start": 0, "end": 0.125}],
        "words": [
            {"text": " Café", "start": 0.125, "end": 0.875, "loss": 0},
            {"text": "世界 ", "start": 0.875, "end": 1.3125, "loss": 0.4375},
        ],
        "loss": 0.21875,
    }
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url == httpx.URL("https://api.elevenlabs.io/v1/forced-alignment")
        assert request.headers["xi-api-key"] == KEY
        content_type = request.headers["content-type"].encode("ascii")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type + b"\r\n\r\n" + request.content
        )
        assert message.get_content_type() == "multipart/form-data"
        assert message.is_multipart()
        assert len(list(message.iter_parts())) == 2
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in message.iter_parts()
        }
        assert parts.keys() == {"text", "file"}
        text, file = parts["text"], parts["file"]
        assert text.get_payload(decode=True).decode("utf-8") == alignment_input["text"]
        assert file.get_filename() == "standalone.wav"
        assert file.get_content_type() == alignment_input["mime_type"]
        assert file.get_payload(decode=True) == AUDIO
        return httpx.Response(200, json=reply)

    result = await execute(ALIGN, alignment_input, handle)
    assert len(seen) == 1
    assert result.output_json == reply
    assert result.files == []


@pytest.mark.parametrize("action", [SPEECH, ALIGN])
@pytest.mark.parametrize(
    "failure,status,unknown,request_id",
    [
        ("timeout", None, True, None),
        ("limited", 429, False, "limited-receipt"),
        ("server", 503, True, "server-receipt"),
        ("broken_json", 200, True, "malformed-receipt"),
        ("broken_payload", 200, True, "malformed-receipt"),
    ],
)
async def test_paid_failure_counts_actual_sends(
    tmp_path, alignment_input, action, failure, status, unknown, request_id
):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        if failure == "timeout":
            raise httpx.ReadTimeout(f"timeout containing {KEY}", request=request)
        if failure == "limited":
            return httpx.Response(
                429,
                headers={"retry-after": "1"},
                json={"detail": {"request_id": request_id, "message": f"echo {KEY}"}},
            )
        if failure == "server":
            return httpx.Response(
                503, headers={"request-id": request_id, "history-item-id": "invalid id"}
            )
        if failure == "broken_json":
            return httpx.Response(200, headers={"request-id": request_id}, content=b"{")
        return httpx.Response(
            200,
            headers={"request-id": request_id, "history-item-id": KEY},
            json={"audio_base64": "!bad-base64!", "words": [{"text": 7}]},
        )

    data = SPEECH_DATA if action == SPEECH else alignment_input
    target = tmp_path / "output"
    with pytest.raises(ConnectorError) as error:
        await execute(action, data, handle, output_dir=target)

    assert len(seen) == 1
    assert error.value.provider_status_code == status
    metadata = error.value.metadata_json
    assert metadata["outcome_unknown"] is unknown
    assert metadata["charge_possible"] is unknown
    assert metadata["retry_safe"] is False
    assert metadata["automatic_retry_count"] == 0
    assert metadata.get("provider_request_id") == request_id
    assert "history_item_id" not in metadata
    if status == 429:
        assert metadata["retry_after"] == 1
    assert KEY not in str(error.value) + str(metadata) + str(error.value.output_json)
    assert not target.exists()


async def test_speech_save_failure_retains_receipts_without_resubmission(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("private disk path")

    monkeypatch.setattr(media.os, "replace", fail)
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"audio_base64": base64.b64encode(AUDIO).decode()},
            headers={"request-id": "completed-request", "history-item-id": "completed-history"},
        )

    with pytest.raises(ConnectorError) as error:
        await execute(SPEECH, SPEECH_DATA, handle, output_dir=tmp_path)
    assert len(seen) == 1
    metadata = error.value.metadata_json
    assert metadata["retry_safe"] is False
    assert metadata["provider_executed"] is True
    assert metadata["charge_possible"] is True
    assert metadata["outcome_unknown"] is False
    assert metadata["provider_request_id"] == "completed-request"
    assert metadata["history_item_id"] == "completed-history"
    assert metadata["reason_code"] == "audio_persistence_failed"
    assert "private disk path" not in str(error.value) + str(metadata)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "override",
    [
        {"voice_id": ".."},
        {"seed": True},
        {"seed": -1},
        {"seed": 2**32},
        {"output_format": "wav_99"},
        {"voice_settings": {"use_speaker_boost": 0}},
        {"pronunciation_dictionary_locators": [{"pronunciation_dictionary_id": "id"}] * 4},
    ],
)
async def test_invalid_speech_input_never_dispatches(tmp_path, override):
    with pytest.raises(ValidationError):
        await execute(
            SPEECH,
            {**SPEECH_DATA, **override},
            lambda request: pytest.fail("invalid speech dispatched"),
            output_dir=tmp_path,
        )


async def test_missing_speech_output_directory_never_dispatches():
    with pytest.raises(ValidationError):
        await execute(SPEECH, SPEECH_DATA, lambda request: pytest.fail("speech dispatched"))


async def test_speech_output_directory_cannot_be_an_existing_file(tmp_path):
    target = tmp_path / "existing.wav"
    target.write_bytes(AUDIO)
    with pytest.raises(ValidationError):
        await execute(
            SPEECH, SPEECH_DATA, lambda request: pytest.fail("speech dispatched"), output_dir=target
        )
    assert target.read_bytes() == AUDIO


@pytest.mark.parametrize("problem", ["missing", "directory", "empty", "oversize", "mime"])
async def test_invalid_alignment_input_never_dispatches(tmp_path, alignment_input, problem):
    if problem == "mime":
        alignment_input["mime_type"] = "audio/wav\r\nX-Injected: yes"
    else:
        invalid_path = tmp_path / "invalid.wav"
        if problem == "directory":
            invalid_path.mkdir()
        elif problem == "empty":
            invalid_path.touch()
        elif problem == "oversize":
            with invalid_path.open("wb") as stream:
                stream.truncate(1_000_000_000)
        alignment_input["audio_path"] = str(invalid_path)
    with pytest.raises(ValidationError):
        await execute(ALIGN, alignment_input, lambda request: pytest.fail("alignment dispatched"))
