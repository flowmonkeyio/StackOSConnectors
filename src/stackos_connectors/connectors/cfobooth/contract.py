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
from stackos_connectors.validation import schema_issues

# CFOBooth libs/contracts/src/shared/errors.ts, sdk837-wire-r6. The JSON
# Schema export omits the message/code refinement; provider text is not public.
PUBLIC_ERROR_MESSAGES = {
    "invalid_request": "The request is invalid.",
    "output_limit": "The complete result exceeds the supported output limit.",
    "unauthenticated": "Authentication is required.",
    "forbidden": "Access is denied.",
    "not_found": "The operation was not found.",
    "conflict": "The operation conflicts with the current state.",
    "run_limit_exceeded": "The run reached a processing limit.",
    "rate_limited": "Too many requests. Try again later.",
    "dependency_unavailable": "A required service is unavailable.",
    "internal_error": "The request could not be completed.",
    "transport_error": "The server could not be reached.",
    "invalid_response": "The server returned an invalid response.",
}

_ISSUE_REQUIRED = {"reason", "path"}
_ISSUE_FIELDS = _ISSUE_REQUIRED | {"field"}
_ISSUE_LIMIT = 4
_MAX_SCHEMA_CHOICES = 16
_ISSUE_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")


def _closed_properties(schema: Any) -> Mapping:
    if (
        isinstance(schema, Mapping)
        and schema.get("type") == "object"
        and schema.get("additionalProperties") is False
        and not schema.get("patternProperties")
    ):
        return schema.get("properties", {})
    return {}


def _issue_token(value: Any) -> bool:
    return isinstance(value, str) and _ISSUE_TOKEN.fullmatch(value) is not None


def _finite_issue_branch(schema: Any) -> bool:
    properties = _closed_properties(schema)
    if not _ISSUE_REQUIRED <= properties.keys() <= _ISSUE_FIELDS:
        return False
    if not _ISSUE_REQUIRED.issubset(schema.get("required", ())):
        return False
    for name, leaf in properties.items():
        if not isinstance(leaf, Mapping):
            return False
        choices = (leaf["const"],) if "const" in leaf else leaf.get("enum", ())
        # A branch binds one reason to its paths and any permitted field names.
        limit = 1 if name == "reason" else _MAX_SCHEMA_CHOICES
        if not 1 <= len(choices) <= limit or not all(map(_issue_token, choices)):
            return False
    return True


def with_query_issues(schema: Mapping, base: dict, issues: Any) -> dict:
    """Extend a safe envelope only when the catalog bounds the whole issue list."""
    if not isinstance(issues, list) or not 1 <= len(issues) <= _ISSUE_LIMIT:
        return base
    error = _closed_properties(schema).get("error")
    array = _closed_properties(error).get("issues")
    if (
        not isinstance(array, Mapping)
        or array.get("type") != "array"
        or "prefixItems" in array
        or array.get("minItems", 0) < 1
        or not 1 <= array.get("maxItems", 0) <= _ISSUE_LIMIT
    ):
        return base
    items = array.get("items")
    if not isinstance(items, Mapping):
        return base
    branches = items.get("oneOf", items.get("anyOf", (items,)))
    if not 1 <= len(branches) <= _MAX_SCHEMA_CHOICES:
        return base
    if not all(map(_finite_issue_branch, branches)):
        return base
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or not _ISSUE_REQUIRED <= issue.keys() <= _ISSUE_FIELDS
            or not all(map(_issue_token, issue.values()))
        ):
            return base
    detailed = {**base, "error": {**base["error"], "issues": issues}}
    try:
        return base if schema_issues(schema, detailed) else detailed
    except RecursionError:
        # A detail-only recursive schema can leave the legacy envelope valid.
        return base


def _valid_email(value: Any) -> bool:
    # VerifiedUser uses shared/primitives.ts emailSchema -> pinned Zod 4.6.5
    # z.email().max(254). JSON Schema's built-in email checker only requires @.
    # Validate the server's wire value without accepting whitespace/control text.
    return (
        isinstance(value, str)
        and len(value) <= 254
        and re.fullmatch(
            r"(?:[A-Za-z0-9_'+\-]+\.)*[A-Za-z0-9_'+\-]*[A-Za-z0-9_+-]"
            r"@(?:[A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}",
            value,
        )
        is not None
    )


@lru_cache
def validator(name: str, *, descriptor: bool = False) -> Draft7Validator:
    schema = json.loads(files(__package__).joinpath("schemas", name + ".json").read_text())
    if descriptor:
        schema = {"$ref": "#/properties/data/properties/operations/items", **schema}
    checker = FormatChecker()
    checker.checks("email")(_valid_email)
    return Draft7Validator(schema, format_checker=checker)


def validate_wire(value: Any, name: str, *, descriptor: bool = False) -> dict:
    value = thaw(value)
    if not validator(name, descriptor=descriptor).is_valid(value):
        raise ValidationError("invalid CFOBooth " + name + " response")
    if name == "catalog-v1":
        for item in [value] if descriptor else value["data"]["operations"]:
            _validate_policy(item)
    return value


def _validate_policy(descriptor: Mapping) -> None:
    """Retain catalog wire refinements from CFO assertOperationPolicy, before filtering."""
    auth, route = descriptor["authentication"], descriptor["route"]
    public = auth["kind"] == "public"
    organization = descriptor["scope"] == "organization"
    if (
        (descriptor["scope"] == "public") != public
        or (not public and organization != (auth["organizationParameter"] is not None))
        or (
            organization
            and not re.match(r"/api/organizations/:organizationId(?:/|$)", route["path"])
        )
    ):
        raise ValidationError("CFOBooth scope conflicts with its authentication binding")
    credentials = [] if public else auth["credentials"]
    if len(credentials) != len(set(credentials)):
        raise ValidationError("duplicate CFOBooth credential kind")
    api_key = "api-key" in credentials
    if api_key and (not organization or descriptor["transport"] != "json"):
        raise ValidationError("CFOBooth API keys require organization JSON operations")
    if descriptor["execution"]["kind"] == "action" and (
        descriptor["transport"] != "json" or not api_key
    ):
        raise ValidationError("CFOBooth actions require explicit API-key JSON eligibility")
    if (
        (descriptor["transport"] == "multipart") != (route["body"] == "multipart")
        or (descriptor["effect"] == "mutation" and descriptor["replay"] != "explicit-only")
        or (route["method"] == "GET" and descriptor["effect"] != "read")
    ):
        raise ValidationError("CFOBooth transport or effect metadata is inconsistent")


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
    if not isinstance(schemas["query"], dict) or schemas["query"].get("type") != "object":
        raise ValidationError("unsupported CFOBooth query parameter schema")
    descriptor["schemas"] = schemas
    return descriptor
