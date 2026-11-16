"""Trackbooth annotations around the shared structural projection."""

from collections.abc import Mapping
from typing import Any

from stackos_connectors.contracts import thaw
from stackos_connectors.shared.schema import operation_input_schema as _operation_input_schema


def _schema_property(raw: Any) -> Any:
    schema = thaw(raw)
    if isinstance(schema, dict):
        if schema.pop("weak", False):
            schema["x_trackbooth_schema_warning"] = schema.get("warning") or "weak live schema"
        schema.pop("warning", None)
        if schema.get("type_script"):
            schema["x_trackbooth_type_script"] = schema["type_script"]
        schema.pop("type_script", None)
    return schema


def operation_input_schema(detail: Mapping[str, Any]) -> dict[str, Any]:
    return _operation_input_schema(
        {
            **detail,
            **{
                key: _schema_property(detail[key])
                for key in ("query_params", "request_body")
                if key in detail
            },
        }
    )


__all__ = ["operation_input_schema"]
