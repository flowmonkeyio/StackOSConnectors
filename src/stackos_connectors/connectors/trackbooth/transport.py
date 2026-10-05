"""Trackbooth path/query serialization and catalog response parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from stackos_connectors.errors import ValidationError

from .contract import PATH_PARAM_RE as _PATH_PARAM_RE
from .contract import JsonObject


def _substitute_path_params(path: str, raw_params: Any) -> str:
    params = raw_params if isinstance(raw_params, Mapping) else {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if not name or name not in params or params[name] in {None, ""}:
            raise ValidationError(f"missing Trackbooth path parameter {name}")
        return quote(str(params[name]), safe="")

    resolved = _PATH_PARAM_RE.sub(replace, path)
    if not resolved.startswith("/"):
        resolved = f"/{resolved}"
    return resolved


def _serialize_query(query: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if not query:
        return []
    params: list[tuple[str, str]] = []
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if item is not None:
                    params.append((str(key), _query_value(item)))
        else:
            params.append((str(key), _query_value(value)))
    return params


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _extract_catalog_items(body: Any) -> list[JsonObject]:
    if isinstance(body, list):
        raw_items = body
    elif isinstance(body, Mapping):
        raw_data = body.get("data")
        if isinstance(raw_data, list):
            raw_items = raw_data
        elif isinstance(raw_data, Mapping):
            raw_items = raw_data.get("endpoints") or raw_data.get("tools") or []
        else:
            raw_items = body.get("endpoints") or body.get("tools") or []
    else:
        raw_items = []
    items = [
        dict(item) for item in raw_items if isinstance(item, Mapping) and item.get("operation_id")
    ]
    return items


def _extract_catalog_export(body: Any) -> JsonObject:
    if not isinstance(body, Mapping):
        raise ValidationError("Trackbooth catalog export response did not include an object")
    raw_data = body.get("data")
    data = raw_data if isinstance(raw_data, Mapping) else body
    raw_endpoints = data.get("endpoints") or data.get("tools") or []
    if not isinstance(raw_endpoints, list):
        raise ValidationError("Trackbooth catalog export response did not include endpoints")
    endpoints = [
        dict(item)
        for item in raw_endpoints
        if isinstance(item, Mapping) and item.get("operation_id")
    ]
    return {
        "version": data.get("version"),
        "generated_at": data.get("generated_at"),
        "catalog_hash": data.get("catalog_hash"),
        "endpoint_count": data.get("endpoint_count"),
        "endpoints": endpoints,
    }


def _extract_operation_detail(body: Any) -> JsonObject:
    if isinstance(body, Mapping):
        raw_data = body.get("data")
        if isinstance(raw_data, Mapping):
            return dict(raw_data)
        raw_operation = body.get("operation") or body.get("endpoint") or body.get("tool")
        if isinstance(raw_operation, Mapping):
            return dict(raw_operation)
        if body.get("operation_id"):
            return dict(body)
    raise ValidationError("Trackbooth operation detail response did not include an operation")
