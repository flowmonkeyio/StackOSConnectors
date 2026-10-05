"""Shared helpers for provider-specific action connectors."""

from __future__ import annotations

import json
import math
from typing import Any

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
    thaw,
)
from stackos_connectors.errors import ValidationError


def issue(path: str, message: str, code: str = "validation_error") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, code=code)


def required_str(payload: dict[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))


def optional_str(payload: dict[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(issue(f"$.{key}", f"{key} must be a string", "type_mismatch"))


def int_range(
    payload: dict[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        issues.append(
            issue(
                f"$.{key}",
                f"{key} must be an integer between {minimum} and {maximum}",
                "range",
            )
        )


def float_range(
    payload: dict[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    minimum: float,
    maximum: float,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        issues.append(
            issue(
                f"$.{key}",
                f"{key} must be a number between {minimum:g} and {maximum:g}",
                "range",
            )
        )


def bool_field(payload: dict[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be a boolean", "type_mismatch"))


def str_list(
    payload: dict[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
    length: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        issues.append(issue(f"$.{key}", f"{key} must be an array of strings", "type_mismatch"))
        return
    if length is not None and len(value) != length:
        issues.append(issue(f"$.{key}", f"{key} must contain {length} items", "length"))


def dict_field(payload: dict[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, dict):
        issues.append(issue(f"$.{key}", f"{key} must be an object", "type_mismatch"))


def required_dict(payload: dict[str, Any], key: str, issues: list[ValidationIssue]) -> None:
    if key not in payload:
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    dict_field(payload, key, issues)


def unknown_operation(request: ConnectorRequest) -> list[ValidationIssue]:
    return [issue("$.operation", f"unsupported operation {request.operation!r}", "enum_mismatch")]


def result(vendor: str, operation: str, result_data: Any, cost_usd: float) -> ConnectorResult:
    output = result_data if isinstance(result_data, dict) else {"data": result_data}
    return ConnectorResult(
        output_json=output,
        metadata_json={"vendor": vendor, "operation": operation},
        cost_cents=cost_cents(cost_usd),
    )


def cost_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, math.ceil(cost_usd * 100))


def credential_payload(request: ConnectorRequest, *, required: bool = True) -> bytes:
    if request.auth is None:
        if required:
            target = request.connector
            raise ValidationError(f"{target} requires a credential")
        return b""
    fields = thaw(request.auth.fields)
    if set(fields) == {"value"} and isinstance(fields["value"], str):
        return fields["value"].encode("utf-8")
    return json.dumps(fields).encode("utf-8")


def credential_config_str(
    request: ConnectorRequest,
    *keys: str,
    label: str,
) -> str:
    config = request.auth.config if request.auth is not None else None
    target = request.connector
    if config is None:
        raise ValidationError(f"{target} credential missing {label}")
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValidationError(f"{target} credential missing {label}")


__all__ = [
    "bool_field",
    "cost_cents",
    "credential_config_str",
    "credential_payload",
    "dict_field",
    "float_range",
    "int_range",
    "issue",
    "optional_str",
    "required_dict",
    "required_str",
    "result",
    "str_list",
    "unknown_operation",
]
