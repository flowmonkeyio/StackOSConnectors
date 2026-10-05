"""Trackbooth bundled assets and live catalog schema projection."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from functools import cached_property
from importlib import resources
from typing import Any

from .contract import PATH_PARAM_RE as _PATH_PARAM_RE
from .contract import JsonObject


class TrackboothAssets:
    """Local Trackbooth API asset loader and schema resolver."""

    def __init__(self) -> None:
        self.stackos_tools = self._read_json("stackos-tools.json")
        self.openapi = self._read_json("openapi.json")
        self.catalog = self._read_json("catalog.json")

    def _read_json(self, name: str) -> Any:
        node = resources.files("stackos_connectors").joinpath(
            "connectors", "trackbooth", "assets", "agent-api", name
        )
        return json.loads(node.read_text(encoding="utf-8"))

    @cached_property
    def tools_by_operation_id(self) -> dict[str, JsonObject]:
        tools_raw = (
            self.stackos_tools.get("tools") if isinstance(self.stackos_tools, dict) else None
        )
        tools = tools_raw if isinstance(tools_raw, list) else []
        return {
            str(tool.get("operation_id")): tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("operation_id")
        }

    @cached_property
    def catalog_by_operation_id(self) -> dict[str, JsonObject]:
        endpoints_raw = self.catalog.get("endpoints") if isinstance(self.catalog, dict) else None
        endpoints = endpoints_raw if isinstance(endpoints_raw, list) else []
        return {
            str(endpoint.get("operation_id")): endpoint
            for endpoint in endpoints
            if isinstance(endpoint, dict) and endpoint.get("operation_id")
        }

    @cached_property
    def openapi_schemas(self) -> dict[str, JsonObject]:
        if not isinstance(self.openapi, dict):
            return {}
        schemas = self.openapi.get("components", {}).get("schemas", {})
        return schemas if isinstance(schemas, dict) else {}

    def operation(self, operation_id: str, live: Mapping[str, Any] | None = None) -> JsonObject:
        static_tool = self.tools_by_operation_id.get(operation_id) or {}
        static_catalog = self.catalog_by_operation_id.get(operation_id) or {}
        if not static_tool and not static_catalog and live is None:
            raise KeyError(operation_id)
        merged: JsonObject = {}
        merged.update(static_catalog)
        merged.update(static_tool)
        if live is not None:
            for key, value in dict(live).items():
                if value is not None:
                    merged[key] = value
        context = _merge_context(static_catalog, static_tool, live)
        if context:
            merged["context"] = context
        return merged

    def summary(self, endpoint: Mapping[str, Any]) -> JsonObject:
        operation_id = str(endpoint.get("operation_id") or "")
        context_raw = endpoint.get("context")
        context: Mapping[str, Any] = context_raw if isinstance(context_raw, Mapping) else {}
        return {
            "operation_id": operation_id,
            "title": endpoint.get("title") or context.get("title") or operation_id,
            "subtitle": context.get("subtitle") or endpoint.get("subtitle"),
            "description": endpoint.get("description") or context.get("subtitle") or "",
            "category": endpoint.get("category") or context.get("category"),
            "tags": endpoint.get("tags") or context.get("tags") or [],
            "method": endpoint.get("method"),
            "path": endpoint.get("path"),
            "permissions": endpoint.get("permissions") or [],
            "roles": endpoint.get("roles") or [],
            "feature_requirements": endpoint.get("feature_requirements") or [],
            "field_groups": endpoint.get("field_groups") or [],
        }

    def detail(self, operation_id: str, live: Mapping[str, Any] | None = None) -> JsonObject:
        endpoint = self.operation(operation_id, live=live)
        summary = self.summary(endpoint)
        path_params = _path_param_details(endpoint)
        query_schema = self.expand_schema(_schema_descriptor(endpoint, "query_schema"))
        body_schema = self.expand_schema(_schema_descriptor(endpoint, "body_schema"))
        response_schema = self.expand_schema(_schema_descriptor(endpoint, "response_schema"))
        weak: list[str] = []
        for label, schema in (
            ("query", query_schema),
            ("body", body_schema),
            ("response", response_schema),
        ):
            if schema and schema.get("weak"):
                weak.append(label)
        return {
            **summary,
            "path_params": path_params,
            "query_params": query_schema,
            "request_body": body_schema,
            "response": response_schema,
            "schema_warnings": weak,
            "source": {
                "bootstrap_manifest": bool(self.tools_by_operation_id.get(operation_id)),
                "static_catalog": bool(self.catalog_by_operation_id.get(operation_id)),
                "live_catalog": live is not None,
            },
        }

    def expand_schema(self, descriptor: Any) -> JsonObject | None:
        return _expand_schema_descriptor(descriptor, self.openapi_schemas)


def _merge_context(
    *sources: Mapping[str, Any] | None,
) -> JsonObject:
    context: JsonObject = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        raw = source.get("context")
        if isinstance(raw, Mapping):
            context.update(dict(raw))
        for key in ("title", "subtitle", "category", "tags"):
            if key in source and source[key] is not None:
                context[key] = source[key]
    return context


def _detail_from_endpoint(
    endpoint: Mapping[str, Any],
    *,
    openapi_schemas: Mapping[str, Any] | None = None,
) -> JsonObject:
    schemas = openapi_schemas or {}
    operation_id = str(endpoint.get("operation_id") or "")
    context_raw = endpoint.get("context")
    context: Mapping[str, Any] = context_raw if isinstance(context_raw, Mapping) else {}
    query_schema = _expand_schema_descriptor(_schema_descriptor(endpoint, "query_schema"), schemas)
    body_schema = _expand_schema_descriptor(_schema_descriptor(endpoint, "body_schema"), schemas)
    response_schema = _expand_schema_descriptor(
        _schema_descriptor(endpoint, "response_schema"),
        schemas,
    )
    schema_warnings: list[str] = []
    for label, schema in (
        ("query", query_schema),
        ("body", body_schema),
        ("response", response_schema),
    ):
        if schema and schema.get("weak"):
            schema_warnings.append(label)
    return {
        "operation_id": operation_id,
        "checksum": endpoint.get("checksum") if isinstance(endpoint.get("checksum"), str) else None,
        "name": endpoint.get("name"),
        "title": endpoint.get("title") or context.get("title") or operation_id,
        "subtitle": context.get("subtitle") or endpoint.get("subtitle"),
        "description": endpoint.get("description") or context.get("subtitle") or "",
        "category": endpoint.get("category") or context.get("category"),
        "tags": endpoint.get("tags") or context.get("tags") or [],
        "method": str(endpoint.get("method") or "").upper(),
        "path": endpoint.get("path"),
        "permissions": endpoint.get("permissions") or [],
        "roles": endpoint.get("roles") or [],
        "feature_requirements": endpoint.get("feature_requirements") or [],
        "field_groups": endpoint.get("field_groups") or [],
        **_risk_metadata(endpoint, context),
        "path_params": _path_param_details(endpoint),
        "query_params": query_schema,
        "request_body": body_schema,
        "response": response_schema,
        "schema_warnings": schema_warnings,
        "source": {
            "live_catalog": True,
            "bootstrap_manifest": False,
            "static_catalog": False,
        },
    }


def _risk_metadata(
    endpoint: Mapping[str, Any],
    context: Mapping[str, Any],
) -> JsonObject:
    metadata: JsonObject = {}
    for key in (
        "risk_level",
        "risk",
        "side_effect",
        "side_effects",
        "read_only",
        "readonly",
        "readOnly",
        "idempotent",
    ):
        if key in endpoint and endpoint[key] is not None:
            metadata[key] = endpoint[key]
        elif key in context and context[key] is not None:
            metadata[key] = context[key]
    return metadata


def _expand_schema_descriptor(
    descriptor: Any,
    openapi_schemas: Mapping[str, Any],
) -> JsonObject | None:
    if not isinstance(descriptor, Mapping):
        return None

    json_schema = descriptor.get("json_schema")
    if isinstance(json_schema, Mapping):
        return deepcopy(dict(json_schema))

    component_name = str(descriptor.get("component_name") or "")
    openapi_component = openapi_schemas.get(component_name)
    if isinstance(openapi_component, Mapping):
        return deepcopy(dict(openapi_component))
    return None


def _schema_descriptor(endpoint: Mapping[str, Any], key: str) -> Any:
    if key in endpoint and endpoint[key] is not None:
        return endpoint[key]
    input_obj = endpoint.get("input")
    if isinstance(input_obj, Mapping):
        mapped_key = "response_schema" if key == "response_schema" else key
        if mapped_key in input_obj and input_obj[mapped_key] is not None:
            return input_obj[mapped_key]
    if key == "response_schema":
        value = endpoint.get("output_schema")
        if value is not None:
            return value
    return None


def _path_param_details(endpoint: Mapping[str, Any]) -> list[JsonObject]:
    raw = endpoint.get("path_params")
    if raw is None:
        input_obj = endpoint.get("input")
        if isinstance(input_obj, Mapping):
            raw = input_obj.get("path_params")
    details: list[JsonObject] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                name = item.get("name")
                if isinstance(name, str):
                    details.append({"name": name, **{k: v for k, v in item.items() if k != "name"}})
            elif isinstance(item, str):
                details.append({"name": item})
    known = {item["name"] for item in details}
    for name in _path_param_names(str(endpoint.get("path") or "")):
        if name not in known:
            details.append({"name": name, "source": "path"})
    return details


def _path_param_names(path: str) -> list[str]:
    names: list[str] = []
    for match in _PATH_PARAM_RE.finditer(path):
        name = match.group(1) or match.group(2)
        if name and name not in names:
            names.append(name)
    return names
