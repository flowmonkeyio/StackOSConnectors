"""Shared embedding keeps each schema resource and structural constraints intact."""

import pytest
from jsonschema import Draft202012Validator

from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.schema import operation_input_schema, project_schema


def test_union_reference_scopes_and_opaque_annotation_data():
    detail = {
        "body_required": True,
        "query_params": {
            "$defs": {"value": {"type": "integer", "minimum": 1}},
            "type": "object",
            "additionalProperties": False,
            "properties": {"value": {"$ref": "#/$defs/value"}},
        },
        "request_body": {
            "$defs": {"value": {"type": "string", "minLength": 2}},
            "anyOf": [{"$ref": "#/$defs/value"}, {"type": "null"}],
            "x-server-refinements": {"$ref": "opaque-rule-name"},
            "examples": [{"$ref": "literal-data"}],
        },
    }
    schema = operation_input_schema(detail)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"query": {"value": 1}, "body": "ok"})
    assert validator.is_valid({"body": None})
    assert not validator.is_valid({"body": "x"})
    assert not validator.is_valid({"body": 1})
    assert not validator.is_valid({"query": {"value": 0}, "body": "ok"})
    assert schema["properties"]["body"]["x-server-refinements"] == {"$ref": "opaque-rule-name"}
    assert schema["properties"]["body"]["examples"] == [{"$ref": "literal-data"}]
    assert detail["request_body"]["anyOf"][0]["$ref"] == "#/$defs/value"


def test_recursive_and_escaped_local_references():
    source = {
        "$defs": {"a/b~c": {"type": "integer"}},
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"$ref": "#/$defs/a~1b~0c"}, "child": {"$ref": "#"}},
    }
    schema = operation_input_schema({"request_body": source, "body_required": True})
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"body": {"value": 1, "child": {"value": 2}}})
    assert not validator.is_valid({"body": {"child": {"value": "wrong"}}})


def test_boolean_and_explicit_optional_envelopes():
    schema = operation_input_schema({"request_body": False, "body_required": True})
    assert not Draft202012Validator(schema).is_valid({})
    assert not Draft202012Validator(schema).is_valid({"body": {}})
    schema = operation_input_schema(
        {
            "request_body": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            "body_required": False,
        }
    )
    assert Draft202012Validator(schema).is_valid({})
    assert not Draft202012Validator(schema).is_valid({"body": {}})


def test_path_parameter_schema_has_its_own_reference_scope():
    schema = operation_input_schema(
        {
            "path_params": [
                {
                    "name": "id",
                    "schema": {
                        "$defs": {"id": {"type": "string", "pattern": "^id-"}},
                        "$ref": "#/$defs/id",
                    },
                }
            ]
        }
    )
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"path_params": {"id": "id-one"}})
    assert not validator.is_valid({"path_params": {"id": "wrong"}})


@pytest.mark.parametrize(
    "schema",
    [
        {"definitions": []},
        {"properties": {"name": {"$ref": "#/title"}}, "title": "not a schema"},
        {"items": [{"type": "string"}]},
        {"contentSchema": {"type": "integer"}},
        {"$defs": {"unused": {"$anchor": "external-scope"}}},
    ],
)
def test_unrepresentable_structures_never_become_catch_all(schema):
    with pytest.raises(ValidationError):
        project_schema(schema)
