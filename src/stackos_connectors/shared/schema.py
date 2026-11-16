"""Lossless structural JSON Schema embedding for declared operation inputs."""

from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote, unquote

from jsonschema import Draft7Validator, Draft202012Validator
from jsonschema.exceptions import SchemaError

from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ValidationError

type JsonSchema = dict[str, Any] | bool

_MAP_SCHEMAS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_LIST_SCHEMAS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SINGLE_SCHEMAS = {
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "items",
    "unevaluatedItems",
    "contains",
    "not",
    "if",
    "then",
    "else",
}
_KEYWORDS = (
    _MAP_SCHEMAS
    | _LIST_SCHEMAS
    | _SINGLE_SCHEMAS
    | {
        "$schema",
        "$ref",
        "$comment",
        "type",
        "enum",
        "const",
        "required",
        "dependentRequired",
        "minProperties",
        "maxProperties",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minContains",
        "maxContains",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
        "discriminator",
    }
)


def _pointer(path: tuple[str, ...]) -> str:
    return "#" + "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in path)


def _schema_nodes(
    schema: JsonSchema, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, JsonSchema]]:
    yield _pointer(path), schema
    if not isinstance(schema, dict):
        return
    for key, value in schema.items():
        if key in _MAP_SCHEMAS:
            for name, child in value.items():
                yield from _schema_nodes(child, (*path, key, name))
        elif key in _LIST_SCHEMAS:
            for index, child in enumerate(value):
                yield from _schema_nodes(child, (*path, key, str(index)))
        elif key in _SINGLE_SCHEMAS:
            yield from _schema_nodes(value, (*path, key))


def project_schema(raw: Any, *, prefix: str = "#") -> JsonSchema:
    """Detach and embed a schema; local pointers retain their original resource scope.

    Resource IDs, anchors, remote references, other dialects and unknown keywords
    are rejected. Extensions and annotation values are opaque, never traversed.
    The prefix is a JSON Pointer fragment before URI percent encoding.
    """
    if not isinstance(raw, Mapping | bool):
        raise ValidationError("operation schema must be an object or boolean")
    schema = thaw(raw)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValidationError("invalid operation JSON Schema") from None
    nodes = dict(_schema_nodes(schema))
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        for key in node:
            if key not in _KEYWORDS and not key.startswith(("x-", "x_")):
                raise ValidationError("unsupported operation schema keyword")
        if "$schema" in node and node["$schema"] != Draft202012Validator.META_SCHEMA["$id"]:
            raise ValidationError("unsupported operation schema dialect")
        if "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#") or unquote(ref) not in nodes:
                raise ValidationError("operation schema requires a supported local reference")
            # Decode only for lookup; the validator decodes the emitted URI once.
            node["$ref"] = "#" + quote(prefix[1:], safe="/~") + ref[1:]
    return schema


def normalize_draft7_schema(raw: Any) -> JsonSchema:
    """Opt in to the shared structural subset of Draft 7 without weakening it.

    Tuple items, dependencies and reference siblings have different semantics
    across dialects and fail explicitly. Annotation values remain opaque.
    Existing consumers of project_schema retain their dialect contract.
    """
    schema = thaw(raw)
    if not isinstance(schema, dict) or schema.get("$schema") != Draft7Validator.META_SCHEMA["$id"]:
        return project_schema(schema)
    try:
        Draft7Validator.check_schema(schema)
    except SchemaError:
        raise ValidationError("invalid Draft 7 operation schema") from None
    modern_only = {
        "$defs",
        "dependentSchemas",
        "dependentRequired",
        "prefixItems",
        "unevaluatedProperties",
        "unevaluatedItems",
        "minContains",
        "maxContains",
    }
    annotations = {"$ref", "$comment", "title", "description", "default", "examples"}

    # Inspect shape before walking: Draft 7 tuple items are not schema objects.
    def convert(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if modern_only & node.keys() or isinstance(node.get("items"), list):
            raise ValidationError("unsupported Draft 7 operation schema feature")
        if "$ref" in node and node.keys() - annotations:
            raise ValidationError("unsupported Draft 7 reference siblings")
        if "$schema" in node:
            if node["$schema"] != Draft7Validator.META_SCHEMA["$id"]:
                raise ValidationError("mixed operation schema dialects")
            del node["$schema"]
        for key, value in node.items():
            if key in _MAP_SCHEMAS:
                for child in value.values():
                    convert(child)
            elif key in _LIST_SCHEMAS:
                for child in value:
                    convert(child)
            elif key in _SINGLE_SCHEMAS:
                convert(value)

    convert(schema)
    return project_schema(schema)


def operation_input_schema(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap complete path/query/body schemas in the connector input envelope."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    if "path_params_schema" in detail:
        if detail.get("path_params"):
            raise ValidationError("operation declares conflicting path schemas")
        if not isinstance(detail.get("path_required", True), bool):
            raise ValidationError("operation path required flag must be boolean")
        properties["path_params"] = project_schema(
            detail["path_params_schema"], prefix="#/properties/path_params"
        )
        if detail.get("path_required", True):
            required.append("path_params")
    path_params = detail.get("path_params")
    if isinstance(path_params, list | tuple) and path_params:
        path_properties = {
            str(item["name"]): project_schema(
                item.get("schema", {"type": "string"}),
                prefix="#/properties/path_params/properties/"
                + str(item["name"]).replace("~", "~0").replace("/", "~1"),
            )
            for item in path_params
            if isinstance(item, Mapping) and item.get("name")
        }
        if path_properties:
            properties["path_params"] = {
                "type": "object",
                "additionalProperties": False,
                "required": list(path_properties),
                "properties": path_properties,
            }
            required.append("path_params")

    for source, target in (("query_params", "query"), ("request_body", "body")):
        raw = detail.get(source)
        explicit = detail.get(f"{target}_required")
        if explicit is not None and not isinstance(explicit, bool):
            raise ValidationError("operation envelope required flag must be boolean")
        if raw is None:
            if explicit:
                raise ValidationError("required operation envelope has no schema")
            continue
        schema = project_schema(raw, prefix=f"#/properties/{target}")
        properties[target] = schema
        if (
            explicit
            if explicit is not None
            else isinstance(schema, dict) and schema.get("required")
        ):
            required.append(target)

    schema = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        schema["required"] = required
    return schema
