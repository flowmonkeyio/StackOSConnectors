"""Independent S1 literals exercise the public declaration and admission boundary."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from stackos_connectors import CallOptions
from stackos_connectors.connectors.trackbooth import declare_action
from stackos_connectors.connectors.trackbooth.schema import operation_input_schema
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ConnectorError, ValidationError

from .test_native import AUTH, client


def structural_contract():
    return json.loads(
        (Path(__file__).parents[1] / "core/fixtures/structural-operation.json").read_text()
    )


def structural_endpoint():
    return {
        "operation_id": "Fixture.preview",
        "method": "POST",
        "path": "/api/fixture/preview",
        "read_only": True,
        "idempotent": False,
        "body_schema": {
            "required": True,
            "json_schema": {
                **structural_contract()["properties"]["body"],
                "$defs": structural_contract()["$defs"],
            },
        },
    }


def test_root_constraints_are_not_replaced_by_permissive_object():
    schema = thaw(declare_action("trackbooth.s1", structural_endpoint()).input_schema)
    body = schema["properties"]["body"]
    assert body["additionalProperties"] is False
    assert body["oneOf"] == [
        {"properties": {"mode": {"const": "preview"}}},
        {"properties": {"mode": {"const": "commit"}}},
    ]
    assert body["minProperties"] == 2 and body["maxProperties"] == 2
    assert body["x-server-refinements"] == {"owner": "server", "rules": ["current-account-budget"]}


def test_explicit_required_body_with_no_required_members():
    endpoint = structural_endpoint()
    endpoint["body_schema"]["json_schema"] = {"type": "object", "additionalProperties": False}
    bound = client().register_actions([declare_action("trackbooth.s1", endpoint)])
    assert bound.validate_data("trackbooth", "trackbooth.s1", {})
    assert not bound.validate_data("trackbooth", "trackbooth.s1", {"body": {}})


@pytest.mark.parametrize(
    "body,allowed",
    [
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"]}}, True),
        (
            {"mode": "commit", "details": {"label": "abcdefgh", "amount": 9.5, "tags": ["a", "b"]}},
            True,
        ),
        ({"mode": "invalid", "details": {"label": "ok", "amount": 1, "tags": ["a"]}}, False),
        (
            {"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"]}, "extra": 1},
            False,
        ),
        (
            {"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"], "extra": 1}},
            False,
        ),
        ({"mode": "preview", "details": {"label": "x", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "abcdefghi", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "OK", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 0.5, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 10, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1.25, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": []}}, False),
        (
            {"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a", "b", "c"]}},
            False,
        ),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a", "a"]}}, False),
    ],
)
def test_s1_independent_admission_outcomes(body, allowed):
    canonical = structural_contract()
    assert Draft202012Validator(canonical).is_valid({"body": body}) is allowed
    declaration = declare_action("trackbooth.s1", structural_endpoint())
    bound = client().register_actions([declaration])
    assert (not bound.validate_data("trackbooth", "trackbooth.s1", {"body": body})) is allowed


@pytest.mark.parametrize("data", [{}, {"body": {}}, {"body": {"mode": "preview"}}, {"extra": 1}])
def test_missing_body_members_and_extra_root_fail(data):
    bound = client().register_actions([declare_action("trackbooth.s1", structural_endpoint())])
    assert bound.validate_data("trackbooth", "trackbooth.s1", data)


def test_declaration_detaches_complete_schema():
    endpoint = structural_endpoint()
    before = deepcopy(endpoint)
    declaration = declare_action("trackbooth.s1", endpoint)
    assert endpoint == before
    endpoint["body_schema"]["json_schema"]["properties"].clear()
    assert "details" in declaration.input_schema["properties"]["body"]["properties"]
    with pytest.raises(TypeError):
        declaration.input_schema["properties"]["body"]["additionalProperties"] = True


def test_extra_root_with_valid_body_is_denied_by_both_contracts():
    data = {
        "body": {"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"]}},
        "extra": True,
    }
    assert not Draft202012Validator(structural_contract()).is_valid(data)
    bound = client().register_actions([declare_action("trackbooth.s1", structural_endpoint())])
    assert bound.validate_data("trackbooth", "trackbooth.s1", data)


def test_response_schema_is_complete_detached_and_immutable():
    endpoint = structural_endpoint()
    response = deepcopy(endpoint["body_schema"]["json_schema"])
    endpoint["response_schema"] = {"json_schema": response}
    declaration = declare_action("trackbooth.s1", endpoint)
    assert thaw(declaration.config["response_schema"]) == response
    response["oneOf"].clear()
    assert len(declaration.config["response_schema"]["oneOf"]) == 2
    with pytest.raises(TypeError):
        declaration.config["response_schema"]["additionalProperties"] = True


@pytest.mark.parametrize("read_only", [True, False])
async def test_post_execution_metadata_and_local_admission(read_only):
    endpoint = structural_endpoint()
    endpoint.update(read_only=read_only, side_effects=["save"] if not read_only else [])
    declaration = declare_action("trackbooth.s1", endpoint)
    assert declaration.metadata["read_only"] is read_only
    assert declaration.metadata["idempotent"] is False
    assert declaration.config["method"] == "POST"
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "mode": "preview",
            "details": {"label": "ok", "amount": 1, "tags": ["a"]},
        }
        return httpx.Response(503, json={"code": "unavailable"})

    bound = client().register_actions([declaration])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "trackbooth",
                "trackbooth.s1",
                {"body": {"mode": "bad"}},
                AUTH,
                CallOptions(http=http),
            )
        assert not calls
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "trackbooth",
                "trackbooth.s1",
                {
                    "body": {
                        "mode": "preview",
                        "details": {"label": "ok", "amount": 1, "tags": ["a"]},
                    }
                },
                AUTH,
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["retry_safe"] is False


@pytest.mark.parametrize(
    "descriptor",
    [
        {"json_schema": "object"},
        {"component_name": "missing"},
        {"details": {"fields": []}},
        {"json_schema": {"type": "object", "additionalProperties": "false"}},
        {"json_schema": {"$ref": "https://example.test/schema"}},
        {"json_schema": {"$ref": "#/$defs/missing"}},
        {"json_schema": {"nullable": True}},
        {"json_schema": {"$dynamicRef": "#node"}},
        {"json_schema": {"$id": "urn:fixture:root"}},
        {"json_schema": {"$schema": "http://json-schema.org/draft-07/schema#"}},
        {"json_schema": {"properties": {"nested": {"unrecognizedConstraint": True}}}},
    ],
)
def test_unsupported_schema_is_rejected_at_declaration(descriptor):
    with pytest.raises(ValidationError):
        declare_action("trackbooth.s1", {**structural_endpoint(), "body_schema": descriptor})


def test_legacy_warning_annotations_and_import_remain_available():
    projected = operation_input_schema(
        {
            "request_body": {
                "type": "object",
                "additionalProperties": False,
                "weak": True,
                "warning": "server refinement",
                "type_script": "FixtureInput",
            }
        }
    )
    assert projected["properties"]["body"] == {
        "type": "object",
        "additionalProperties": False,
        "x_trackbooth_schema_warning": "server refinement",
        "x_trackbooth_type_script": "FixtureInput",
    }


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "null"}, None),
        ({"type": "array", "minItems": 1, "items": {"type": "integer"}}, [1]),
    ],
)
async def test_non_object_body_is_preserved_through_the_wire(schema, value):
    endpoint = structural_endpoint()
    endpoint["body_schema"] = {"required": True, "json_schema": schema}
    bound = client().register_actions([declare_action("trackbooth.s1", endpoint)])

    def handle(request):
        assert request.content == json.dumps(value).encode()
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await bound.execute(
            "trackbooth", "trackbooth.s1", {"body": value}, AUTH, CallOptions(http=http)
        )
