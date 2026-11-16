"""Independent S1 literals exercise the public declaration and admission boundary."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from stackos_connectors.contracts import thaw
from stackos_connectors.connectors.trackbooth import declare_action

from .test_native import client


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
        "body_schema": {"required": True, "json_schema": structural_contract()["properties"]["body"]},
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
    assert body["x-server-refinements"] == {
        "owner": "server", "rules": ["current-account-budget"]
    }


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
        ({"mode": "commit", "details": {"label": "abcdefgh", "amount": 9.5, "tags": ["a", "b"]}}, True),
        ({"mode": "invalid", "details": {"label": "ok", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"]}, "extra": 1}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a"], "extra": 1}}, False),
        ({"mode": "preview", "details": {"label": "x", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "abcdefghi", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "OK", "amount": 1, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 0.5, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 10, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1.25, "tags": ["a"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": []}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a", "b", "c"]}}, False),
        ({"mode": "preview", "details": {"label": "ok", "amount": 1, "tags": ["a", "a"]}}, False),
    ],
)
def test_s1_independent_admission_outcomes(body, allowed):
    canonical = structural_contract()
    # The standalone canonical body has its own document-local $defs.
    assert Draft202012Validator(canonical["properties"]["body"]).is_valid(body) is allowed
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
