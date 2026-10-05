"""Provider schema projection for explicit operation declarations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import JsonObject


def operation_input_schema(detail: Mapping[str, Any]) -> JsonObject:
    properties: JsonObject = {}
    required: list[str] = []
    path_params = detail.get("path_params")
    if isinstance(path_params, list) and path_params:
        path_properties = {
            str(item.get("name")): {"type": "string"}
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

    query_schema = _schema_property(detail.get("query_params"))
    if query_schema is not None:
        properties["query"] = query_schema
        if query_schema.get("required"):
            required.append("query")

    body_schema = _schema_property(detail.get("request_body"))
    if body_schema is not None:
        properties["body"] = body_schema
        if body_schema.get("required"):
            required.append("body")

    schema: JsonObject = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _schema_property(raw: Any) -> JsonObject | None:
    if not isinstance(raw, Mapping):
        return None
    schema: JsonObject = {
        "type": "object",
        "additionalProperties": True,
    }
    properties = raw.get("properties")
    if isinstance(properties, Mapping) and properties:
        schema["properties"] = dict(properties)
    required = raw.get("required")
    if isinstance(required, list) and required:
        schema["required"] = [str(item) for item in required if isinstance(item, str)]
    if raw.get("weak"):
        schema["x_trackbooth_schema_warning"] = raw.get("warning") or "weak live schema"
    if raw.get("type_script"):
        schema["x_trackbooth_type_script"] = raw["type_script"]
    return schema


def _schema_required_fields(raw: Any) -> list[str]:
    if not isinstance(raw, Mapping):
        return []
    required = raw.get("required")
    if isinstance(required, list):
        return [str(item) for item in required if isinstance(item, str)]
    return []
