"""Common immutable REST declaration; providers own catalog and destination policy."""

from collections.abc import Mapping
from typing import Any

from stackos_connectors.contracts import ActionDefinition, AuthMethodDefinition

from .schema import operation_input_schema, project_schema


def declare_operation(
    *,
    connector: str,
    key: str,
    detail: Mapping[str, Any],
    auth_methods: tuple[AuthMethodDefinition, ...],
) -> ActionDefinition:
    """Project a provider-normalized operation into the canonical frozen contract."""
    operation_id = detail["operation_id"]
    return ActionDefinition(
        connector=connector,
        key=key,
        operation="operation.execute",
        auth_methods=auth_methods,
        input_schema=operation_input_schema(detail),
        output_schema={"type": "object", "additionalProperties": True},
        config={
            "operation_id": operation_id,
            "method": detail["method"],
            "path": detail["path"],
            "has_body": detail.get("request_body") is not None,
            "response_schema": project_schema(detail["response"])
            if detail.get("response") is not None
            else None,
        },
        description=str(detail.get("description") or detail.get("title") or operation_id),
        guidance=(
            "Pass this operation's path parameters, query and body. The registered route is fixed."
        ),
        metadata={
            "name": str(detail.get("title") or operation_id),
            **{
                key: detail[key]
                for key in (
                    "checksum",
                    "permissions",
                    "roles",
                    "feature_requirements",
                    "field_groups",
                    "risk_level",
                    "risk",
                    "side_effect",
                    "side_effects",
                    "read_only",
                    "readonly",
                    "readOnly",
                    "idempotent",
                )
                if key in detail
            },
        },
    )
