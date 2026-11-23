import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, ValidationError

SEARCH = "elevenlabs.library.search"
ADD = "elevenlabs.library.add"
KEY = "synthetic-library-key"
AUTH = ConnectorAuth(method="api_key", fields={"api_key": KEY})
ADD_INPUT = {"public_owner_id": "owner/2?", "voice_id": "voice%2/", "new_name": " Café  "}


async def execute(action, data, handle):
    client = get_default_client()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        result = await client.execute("elevenlabs", action, data, AUTH, CallOptions(http=http))
        assert not http.is_closed
    schema = client.describe("elevenlabs", action)["actions"][0]["output_schema"]
    Draft202012Validator(schema).validate(result.output_json)
    assert not result.files
    return result.output_json


def test_catalog_classifies_search_as_read_and_add_as_write():
    client = get_default_client()
    search = client.describe("elevenlabs", SEARCH)["actions"][0]
    add = client.describe("elevenlabs", ADD)["actions"][0]
    assert search["read_only"] is True
    assert search["risk_level"] == "read"
    assert add["read_only"] is False
    assert add["risk_level"] == "write"
    assert search["auth_optional"] is add["auth_optional"] is False


async def test_search_preserves_second_page_duplicates_and_price_metadata():
    voices = [
        {
            "public_owner_id": "owner-1",
            "voice_id": "voice-1",
            "name": "Duplicate",
            "preview_url": "https://preview.test/file.mp3?signature=opaque",
            "notice_period": 30,
            "rate": 1.5,
            "fiat_rate": 0.22,
        },
        {
            "public_owner_id": "owner-2",
            "voice_id": "voice-2",
            "name": "Duplicate",
            "preview_url": None,
            "notice_period": 0,
            "rate": None,
            "fiat_rate": None,
        },
        {"public_owner_id": "owner-3", "voice_id": "voice-3", "name": "Third"},
        {
            "public_owner_id": "owner-4",
            "voice_id": "voice-4",
            "name": "Fourth",
            "notice_period": None,
            "rate": 0,
            "fiat_rate": 0,
        },
    ]
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert (
            str(request.url.copy_with(query=None)) == "https://api.elevenlabs.io/v1/shared-voices"
        )
        assert dict(request.url.params) == {"search": "Café + voice", "page": "1", "page_size": "4"}
        assert request.headers["xi-api-key"] == KEY
        assert request.content == b""
        return httpx.Response(
            200,
            json={"has_more": True, "voices": [{**row, "extra": "ignored"} for row in voices]},
        )

    data = {"search": "Café + voice", "page": 1, "page_size": 4}
    assert await execute(SEARCH, data, handle) == {"has_more": True, "voices": voices}
    assert len(seen) == 1


async def test_search_defaults_to_first_page_and_preserves_empty_result():
    def handle(request):
        assert request.method == "GET"
        assert dict(request.url.params) == {"page": "0", "page_size": "30"}
        return httpx.Response(200, json={"has_more": False, "voices": []})

    assert await execute(SEARCH, {"search": None}, handle) == {"has_more": False, "voices": []}


@pytest.mark.parametrize("invalid_fields", [{"public_owner_id": None}, {"rate": "1.5"}])
async def test_search_rejects_invalid_provider_identity_or_price(invalid_fields):
    row = {"public_owner_id": "owner", "voice_id": "voice", "name": "Shared", **invalid_fields}

    def handle(request):
        return httpx.Response(200, json={"has_more": False, "voices": [row]})

    with pytest.raises(ConnectorError) as caught:
        await execute(SEARCH, {}, handle)
    assert caught.value.metadata_json["reason_code"] == "invalid_response"


def assert_add_request(request):
    assert request.method == "POST"
    assert str(request.url) == "https://api.elevenlabs.io/v1/voices/add/owner%2F2%3F/voice%252%2F"
    assert request.headers["xi-api-key"] == KEY
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"new_name": " Café  "}


async def test_add_sends_one_exact_post_and_returns_the_new_voice_id():
    seen = []

    def handle(request):
        seen.append(request)
        assert_add_request(request)
        return httpx.Response(200, json={"voice_id": "new-voice-id", "extra": "ignored"})

    assert await execute(ADD, ADD_INPUT, handle) == {"voice_id": "new-voice-id"}
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "outcome_unknown"),
    [(403, False), (429, False), (503, True), (None, True), (200, True), (307, True)],
)
async def test_add_does_not_repeat_on_denial_or_ambiguous_failure(status, outcome_unknown):
    seen = []

    def handle(request):
        seen.append(request)
        assert_add_request(request)
        if status is None:
            raise httpx.ReadTimeout(f"timeout containing {KEY}", request=request)
        if status == 307:
            return httpx.Response(status, headers={"Location": "https://redirect.test/"})
        return httpx.Response(status, json={"detail": {"status": "entitlement", "message": KEY}})

    with pytest.raises(ConnectorError) as caught:
        await execute(ADD, ADD_INPUT, handle)

    error = caught.value
    assert len(seen) == 1
    assert error.provider_status_code == status
    assert error.metadata_json["outcome_unknown"] is outcome_unknown
    assert error.metadata_json["retry_safe"] is False
    assert error.metadata_json["automatic_retry_count"] == 0
    assert "charge_possible" not in error.metadata_json
    if status == 403:
        assert error.provider_error == {"reason_code": "permission_denied"}
    assert KEY not in str(error) + repr(error.output_json) + repr(error.metadata_json)


@pytest.mark.parametrize(
    ("action", "data"),
    [
        (SEARCH, {"page": -1}),
        (SEARCH, {"page_size": 0}),
        (SEARCH, {"page_size": 101}),
        (ADD, {"voice_id": "voice-2", "new_name": "Name"}),
        (ADD, {**ADD_INPUT, "public_owner_id": ".."}),
        (ADD, {**ADD_INPUT, "voice_id": "."}),
        (ADD, {**ADD_INPUT, "new_name": " "}),
    ],
)
async def test_catalog_rejects_invalid_input_before_transport(action, data):
    def forbidden(request):
        pytest.fail("invalid inputs must not reach the provider")

    with pytest.raises(ValidationError):
        await execute(action, data, forbidden)
