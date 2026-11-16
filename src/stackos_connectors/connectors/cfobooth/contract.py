"""Versioned wire validation; business operations remain server catalog-owned."""

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.schema import normalize_draft7_schema


@lru_cache
def validator(name: str, *, descriptor: bool = False) -> Draft7Validator:
    schema = json.loads(files(__package__).joinpath("schemas", name + ".json").read_text())
    if descriptor:
        schema = {"$ref": "#/properties/data/properties/operations/items", **schema}
    return Draft7Validator(schema, format_checker=FormatChecker())


def validate_wire(value: Any, name: str, *, descriptor: bool = False) -> dict:
    value = thaw(value)
    if not validator(name, descriptor=descriptor).is_valid(value):
        raise ValidationError("invalid CFOBooth " + name + " response")
    return value


def eligible(descriptor: Mapping) -> bool:
    auth = descriptor["authentication"]
    return (
        descriptor["execution"]["kind"] == "action"
        and descriptor["scope"] == "organization"
        and descriptor["transport"] == "json"
        and descriptor["route"]["body"] in {"none", "json"}
        and auth["kind"] == "authenticated"
        and "api-key" in auth["credentials"]
        and auth["organizationParameter"] == "organizationId"
    )


def normalize_descriptor(raw: Mapping) -> dict:
    descriptor = validate_wire(raw, "catalog-v1", descriptor=True)
    if not eligible(descriptor):
        raise ValidationError("CFOBooth operation is not eligible for API-key execution")
    route = descriptor["route"]
    path = route["path"]
    if not re.fullmatch(
        r"/api/organizations/:organizationId(?:/(?:[A-Za-z0-9][A-Za-z0-9._~-]*|:[A-Za-z][A-Za-z0-9]*))*",
        path,
    ):
        raise ValidationError("CFOBooth operation must bind the organization path")
    names = re.findall(r":([A-Za-z][A-Za-z0-9]*)", path)
    if len(names) != len(set(names)) or any(p in {".", ".."} for p in path.split("/")):
        raise ValidationError("invalid CFOBooth route parameters")
    schemas = {
        key: normalize_draft7_schema(value) if value is not None else None
        for key, value in descriptor["schemas"].items()
    }
    params = schemas["params"]
    # Hidden binding is lossless for this finite object shape. Other structural
    # forms need explicit specialization and fail rather than being weakened.
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "$defs",
        "definitions",
        "title",
        "description",
        "readOnly",
        "writeOnly",
        "examples",
        "$comment",
    }
    if (
        not isinstance(params, dict)
        or params.get("type") != "object"
        or params.keys() - allowed
        or set(params.get("properties", {})) != set(names)
        or set(params.get("required", [])) != set(names)
    ):
        raise ValidationError("unsupported CFOBooth organization parameter schema")
    if (route["body"] == "none") != (schemas["body"] is None):
        raise ValidationError("CFOBooth body binding conflicts with its schema")
    if route["method"] == "GET" and schemas["body"] is not None:
        raise ValidationError("CFOBooth GET body is unsupported")
    if descriptor["effect"] == "mutation" and descriptor["replay"] != "explicit-only":
        raise ValidationError("CFOBooth mutations require explicit-only replay")
    descriptor["schemas"] = schemas
    return descriptor
