"""Local JSON validation; schema diagnostics never echo caller values."""

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import ValidationIssue, thaw


def schema_issues(
    schema: Mapping[str, Any], value: Any, *, path: str = "$"
) -> list[ValidationIssue]:
    validator = Draft202012Validator(thaw(schema))
    return [
        ValidationIssue(
            path=path
            + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
            ),
            message=f"value does not satisfy {error.validator} constraint",
            code=str(error.validator),
        )
        for error in validator.iter_errors(value)
    ]
