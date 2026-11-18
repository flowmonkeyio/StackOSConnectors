import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, ValidationError

AUTH = ConnectorAuth(method="api_key", fields={"api_key": "action-canary"})


async def execute(operation, data, handle):
    client = get_default_client()
    key = f"elevenlabs.{operation}"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await client.execute("elevenlabs", key, data, AUTH, CallOptions(http=http))
        assert not http.is_closed
    schema = client.describe("elevenlabs", key)["actions"][0]["output_schema"]
    Draft202012Validator(schema).validate(result.output_json)
    assert result.cost_cents == 0
    assert not result.files
    return result.output_json


def get_fixture(path, reply, *, status=200):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.elevenlabs.io"
        assert request.url.raw_path.split(b"?", 1)[0].decode() == path
        assert request.headers["xi-api-key"] == "action-canary"
        assert "authorization" not in request.headers
        assert not request.content
        return httpx.Response(status, json=reply)

    return handle, seen


async def test_subscription_projects_usage_including_zero():
    subscription = {"tier": "free", "character_count": 0, "character_limit": 0}
    handle, seen = get_fixture(
        "/v1/user/subscription", {**subscription, "open_invoices": [{"amount_due_cents": 900}]}
    )
    assert await execute("subscription.get", {}, handle) == subscription
    assert len(seen) == 1
    assert not seen[0].url.query


@pytest.mark.parametrize(
    ("status", "reason"), [(401, "authentication_failed"), (403, "permission_denied")]
)
async def test_subscription_distinguishes_denial_without_echoing_key(status, reason):
    handle, seen = get_fixture(
        "/v1/user/subscription", {"detail": "Key action-canary rejected"}, status=status
    )
    with pytest.raises(ConnectorError) as caught:
        await execute("subscription.get", {}, handle)
    assert caught.value.provider_status_code == status
    assert caught.value.provider_error == {"reason_code": reason}
    assert "action-canary" not in str(caught.value)
    assert "action-canary" not in json.dumps(caught.value.output_json)
    assert "action-canary" not in json.dumps(caught.value.metadata_json)
    assert caught.value.__cause__ is None
    assert len(seen) == 1


async def test_subscription_does_not_forward_the_key_through_redirects():
    def handle(request):
        assert request.url == httpx.URL("https://api.elevenlabs.io/v1/user/subscription")
        return httpx.Response(307, headers={"Location": "https://other.test/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        with pytest.raises(ConnectorError) as caught:
            await get_default_client().execute(
                "elevenlabs", "elevenlabs.subscription.get", {}, AUTH, CallOptions(http=http)
            )
    assert caught.value.provider_status_code == 307


async def test_all_models_keep_ids_provider_capabilities_and_current_limit():
    models = [
        {
            "model_id": "eleven-example-tts",
            "name": "Example multilingual model",
            "can_do_text_to_speech": True,
            "can_use_style": True,
            "can_use_speaker_boost": False,
            "languages": [{"language_id": "pt", "name": "Portuguese"}],
            "maximum_text_length_per_request": 4500,
        },
        {"model_id": "eleven-example-stt", "can_do_text_to_speech": False},
        {"model_id": "future-unknown-id", "name": "Future model"},
    ]
    handle, seen = get_fixture(
        "/v1/models", [{**models[0], "max_characters_request_free_user": 8}, *models[1:]]
    )
    assert await execute("models.list", {}, handle) == {"models": models}
    assert len(seen) == 1
    assert not seen[0].url.query


@pytest.mark.parametrize(
    ("data", "query", "voices", "has_more", "next_token"),
    [
        ({}, {"page_size": "10"}, [{"voice_id": "first", "name": "First"}], True, "page-2"),
        (
            {"page_size": 2, "page_token": "page+2/="},
            {"page_size": "2", "next_page_token": "page+2/="},
            [{"voice_id": "second", "name": "Second"}, {"voice_id": "unnamed"}],
            True,
            "last-page",
        ),
        (
            {"page_size": 2, "page_token": "last-page"},
            {"page_size": "2", "next_page_token": "last-page"},
            [],
            False,
            None,
        ),
    ],
)
async def test_voices_return_one_page(data, query, voices, has_more, next_token):
    handle, seen = get_fixture(
        "/v2/voices",
        {
            "voices": [{**voice, "preview_url": "https://preview.test"} for voice in voices],
            "has_more": has_more,
            **({"next_page_token": next_token} if next_token is not None else {}),
        },
    )
    assert await execute("voices.list", data, handle) == {
        "voices": voices,
        "page_size": data.get("page_size", 10),
        "page_token": data.get("page_token"),
        "has_more": has_more,
        "next_page_token": next_token,
    }
    assert len(seen) == 1
    assert dict(seen[0].url.params) == query


async def test_default_voice_settings_preserve_absence_null_zero_and_false():
    settings = {"stability": 0, "similarity_boost": None, "style": 0, "use_speaker_boost": False}
    handle, seen = get_fixture("/v1/voices/settings/default", settings)
    assert await execute("voices.settings.default.get", {}, handle) == settings
    assert len(seen) == 1


async def test_voice_settings_preserve_saved_values_and_eligible_clone_strength():
    settings = {
        "stability": 0.4,
        "similarity_boost": 0.9,
        "style": None,
        "speed": 1.15,
        "use_speaker_boost": False,
        "clone_strength": 0,
    }
    handle, seen = get_fixture("/v1/voices/opaque%2Fvoice%3F%23/settings", settings)
    assert await execute("voices.settings.get", {"voice_id": "opaque/voice?#"}, handle) == settings
    assert len(seen) == 1
    assert not seen[0].url.query


@pytest.mark.parametrize(
    ("operation", "data"),
    [
        ("models.list", {"model_id": "local-filter"}),
        ("voices.list", {"page_size": 101}),
        ("voices.list", {"page_size": True}),
        ("voices.list", {"page_token": ""}),
        ("voices.settings.get", {}),
        ("voices.settings.get", {"voice_id": ".."}),
    ],
)
async def test_catalog_validates_discovery_inputs_before_http(operation, data):
    def forbidden(request):
        pytest.fail("validation must reject before sending HTTP")

    with pytest.raises(ValidationError):
        await execute(operation, data, forbidden)
