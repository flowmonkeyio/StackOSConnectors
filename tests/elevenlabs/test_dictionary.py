import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, ValidationError

ACTION = "elevenlabs.dictionaries.create"
URL = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-rules"
KEY = "synthetic-dictionary-key"
AUTH = ConnectorAuth(method="api_key", fields={"api_key": KEY})
ALIAS = {"type": "alias", "string_to_replace": "UN", "alias": "United Nations"}
PHONEME = {
    "type": "phoneme",
    "string_to_replace": "Café",
    "phoneme": "kafe",
    "alphabet": "ipa",
}
CREATE_INPUT = {
    "name": " Café names ",
    "rules": [{**ALIAS, "case_sensitive": False}, {**PHONEME, "word_boundaries": False}],
}
IDENTIFIERS = {"id": " Dict-É_01 ", "version_id": "v.02+rév"}


async def execute(data, handle):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        result = await get_default_client().execute(
            "elevenlabs", ACTION, data, AUTH, CallOptions(http=http)
        )
        assert not http.is_closed
    return result


def test_catalog_describes_authenticated_dictionary_write():
    description = get_default_client().describe("elevenlabs", ACTION)["actions"][0]
    assert description["risk_level"] == "write"
    assert description["read_only"] is False
    assert description["auth_optional"] is False
    assert [method["key"] for method in description["auth_methods"]] == ["api_key"]
    Draft202012Validator(description["input_schema"]).validate(CREATE_INPUT)


async def test_create_sends_both_rules_in_order_and_returns_exact_identifiers():
    seen = []

    def handle(request):
        seen.append((request.method, str(request.url)))
        assert request.headers["xi-api-key"] == KEY
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == CREATE_INPUT
        return httpx.Response(200, json={**IDENTIFIERS, "name": "Café", "version_rules_num": 2})

    result = await execute(CREATE_INPUT, handle)

    assert seen == [("POST", URL)]
    assert result.output_json == IDENTIFIERS
    assert not result.files
    schema = get_default_client().describe("elevenlabs", ACTION)["actions"][0]["output_schema"]
    Draft202012Validator(schema).validate(result.output_json)


@pytest.mark.parametrize(
    "rules",
    [
        [],
        "alias",
        [None],
        [{"string_to_replace": "UN", "alias": "United Nations"}],
        [{**ALIAS, "type": "unknown"}],
        [{"type": "alias", "string_to_replace": "UN"}],
        [{**ALIAS, "alias": 123}],
        [{**ALIAS, "alias": ""}],
        [{**ALIAS, "string_to_replace": " "}],
        [{**ALIAS, "phoneme": "ju"}],
        [{**ALIAS, "word_boundaries": "false"}],
        [{"type": "phoneme", "string_to_replace": "Café", "phoneme": "kafe"}],
        [{"type": "phoneme", "string_to_replace": "Café", "alphabet": "ipa"}],
        [{**PHONEME, "phoneme": None}],
        [{**PHONEME, "alphabet": 123}],
        [{**PHONEME, "alphabet": " "}],
        [ALIAS, {**PHONEME, "alias": "coffee"}],
    ],
)
async def test_create_rejects_malformed_rules_before_dispatch(rules):
    data = {"name": "Names", "rules": rules}

    def forbidden(_request):
        pytest.fail("invalid rules must not reach the provider")

    client = get_default_client()
    assert client.validate_data("elevenlabs", ACTION, data)
    with pytest.raises(ValidationError, match="input validation failed"):
        await execute(data, forbidden)


@pytest.mark.parametrize(
    ("status", "body", "outcome_unknown"),
    [
        (403, {"detail": {"message": KEY}}, False),
        (429, {}, False),
        (503, {}, True),
        (None, None, True),
        (200, {"version_id": "version-only"}, True),
        (200, {"id": "dict-only"}, True),
        (200, {"id": "dict", "version_id": " "}, True),
        (200, {"id": 24, "version_id": "version"}, True),
    ],
    ids=[
        "denial",
        "rate-limit",
        "server-error",
        "timeout",
        "no-id",
        "no-version",
        "blank",
        "number",
    ],
)
async def test_create_sends_one_post_on_failure(status, body, outcome_unknown):
    seen = []

    def handle(request):
        seen.append((request.method, str(request.url)))
        if status is None:
            raise httpx.ReadTimeout(f"timeout containing {KEY}", request=request)
        return httpx.Response(status, json=body, headers={"request-id": "dictionary-request"})

    with pytest.raises(ConnectorError) as caught:
        await execute(CREATE_INPUT, handle)

    error = caught.value
    assert seen == [("POST", URL)]
    assert error.provider_status_code == status
    assert error.metadata_json["outcome_unknown"] is outcome_unknown
    assert error.metadata_json["retry_safe"] is False
    assert error.metadata_json["automatic_retry_count"] == 0
    assert "charge_possible" not in error.metadata_json
    if status is not None:
        assert error.metadata_json["provider_request_id"] == "dictionary-request"
    if status == 403:
        assert error.provider_error["reason_code"] == "permission_denied"
    if status == 200:
        assert error.metadata_json["reason_code"] == "invalid_response"
    assert KEY not in str(error) + repr(error.output_json) + repr(error.metadata_json)
