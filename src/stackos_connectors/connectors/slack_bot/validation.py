"""Native Slack parameter validation, independent of caller profile policy."""

from collections.abc import Mapping
from typing import Any

from stackos_connectors.contracts import ValidationIssue
from stackos_connectors.shared.provider_utils import issue, unknown_operation

from .constants import (
    _MAX_ACTION_BLOCK_ELEMENTS,
    _MAX_BLOCKS,
    _MAX_BUTTON_ACTION_ID_CHARS,
    _MAX_BUTTON_TEXT_CHARS,
    _MAX_BUTTON_URL_CHARS,
    _MAX_BUTTON_VALUE_CHARS,
)
from .payloads import FIELDS


def validate_request(request):
    payload = request.input_json
    if request.operation not in {*FIELDS, "file.upload"}:
        return unknown_operation(request)
    issues = []
    if request.operation in {
        "message.send",
        "reaction.add",
        "message.delete",
        "conversation.info",
        "conversation.members",
        "conversation.history",
        "file.upload",
    } and (not isinstance(payload.get("channel"), str) or not payload["channel"].strip()):
        issues.append(issue("$.channel", "channel is required", "required"))
    if (
        request.operation == "message.send"
        and not payload.get("text")
        and not payload.get("blocks")
    ):
        issues.append(issue("$", "text or blocks is required", "required"))
    if (
        request.operation == "conversation.open"
        and not payload.get("channel")
        and not payload.get("users")
    ):
        issues.append(issue("$", "channel or users is required", "required"))
    if request.operation == "message.send":
        _blocks(payload.get("blocks"), issues)
    return issues


def _blocks(value: Any, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(issue("$.blocks", "blocks must be an array", "type_error"))
        return
    if len(value) > _MAX_BLOCKS:
        issues.append(issue("$.blocks", f"blocks must contain at most {_MAX_BLOCKS} items"))
    for block_index, block in enumerate(value):
        if not isinstance(block, Mapping):
            issues.append(issue(f"$.blocks[{block_index}]", "block must be an object"))
            continue
        if block.get("type") != "actions":
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    "actions block elements must be an array",
                    "type_error",
                )
            )
            continue
        if len(elements) > _MAX_ACTION_BLOCK_ELEMENTS:
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    f"actions block may contain at most {_MAX_ACTION_BLOCK_ELEMENTS} elements",
                    "length",
                )
            )
        for element_index, element in enumerate(elements):
            _button_element(
                element,
                issues,
                f"$.blocks[{block_index}].elements[{element_index}]",
            )


def _button_element(value: Any, issues: list[ValidationIssue], path: str) -> None:
    if not isinstance(value, Mapping) or value.get("type") != "button":
        return
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        issues.append(issue(f"{path}.action_id", "button action_id is required", "required"))
    elif len(action_id) > _MAX_BUTTON_ACTION_ID_CHARS:
        issues.append(
            issue(
                f"{path}.action_id",
                f"button action_id must be at most {_MAX_BUTTON_ACTION_ID_CHARS} characters",
                "length",
            )
        )
    text = value.get("text")
    if not isinstance(text, Mapping) or not _has_text(text.get("text")):
        issues.append(issue(f"{path}.text", "button text object is required", "required"))
    elif len(str(text.get("text"))) > _MAX_BUTTON_TEXT_CHARS:
        issues.append(
            issue(
                f"{path}.text.text",
                f"button text must be at most {_MAX_BUTTON_TEXT_CHARS} characters",
                "length",
            )
        )
    button_value = value.get("value")
    if button_value is not None:
        if not isinstance(button_value, str):
            issues.append(issue(f"{path}.value", "button value must be a string", "type_error"))
        else:
            if len(button_value) > _MAX_BUTTON_VALUE_CHARS:
                issues.append(
                    issue(
                        f"{path}.value",
                        f"button value must be at most {_MAX_BUTTON_VALUE_CHARS} characters",
                        "length",
                    )
                )
    url = value.get("url")
    if url is not None:
        if not isinstance(url, str):
            issues.append(issue(f"{path}.url", "button url must be a string", "type_error"))
        elif len(url) > _MAX_BUTTON_URL_CHARS:
            issues.append(
                issue(
                    f"{path}.url",
                    f"button url must be at most {_MAX_BUTTON_URL_CHARS} characters",
                    "length",
                )
            )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
