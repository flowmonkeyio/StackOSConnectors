"""Shared Google multipart encoding, correlation and safe protocol diagnostics."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from typing import Any
from uuid import uuid4

import httpx

from stackos_connectors.redaction import redact_secret_values, redact_secrets


def batch_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = sum(item["outcome"] == "success" for item in items)
    failed = sum(item["outcome"] == "error" for item in items)
    unknown = len(items) - succeeded - failed
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "succeeded": succeeded,
            "failed": failed,
            "unknown": unknown,
        },
        "partial_success": 0 < succeeded < len(items),
        "retry_safe": False,
        "reconcile_before_retry": bool(failed or unknown),
    }


def _unknown_item(
    index: int, operation: str, reason: str, status: int | None = None
) -> dict[str, Any]:
    return {
        "index": index,
        "operation": operation,
        "status": status,
        "outcome": "unknown",
        "provider_error": {"message": reason},
    }


def encode_http_request(method: str, path: str, body: dict[str, Any] | None) -> bytes:
    nested = f"{method} {path} HTTP/1.1\r\n"
    if body is not None:
        nested += "Content-Type: application/json\r\n"
    nested += "\r\n" + (json.dumps(body, ensure_ascii=True) if body is not None else "")
    return nested.encode()


def encode_batch(calls: list[tuple[str, str, dict[str, Any] | None]]) -> tuple[bytes, str]:
    boundary = f"stackos_{uuid4().hex}"
    parts = []
    for index, (method, path, body) in enumerate(calls):
        nested = encode_http_request(method, path, body).decode()
        parts.append(
            f"--{boundary}\r\nContent-Type: application/http\r\n"
            f"Content-ID: <item-{index}>\r\n\r\n{nested}\r\n"
        )
    return ("".join(parts) + f"--{boundary}--\r\n").encode(), boundary


def _provider_ids(headers: Any) -> dict[str, str]:
    return {
        key: value
        for key in ("x-request-id", "request-id", "x-guploader-uploadid")
        if isinstance(value := headers.get(key), str)
        and re.fullmatch(r"[A-Za-z0-9._:/=-]{1,200}", value)
    }


def batch_retry_after(value: Any) -> float | None:
    """Keep finite non-negative numeric advice; never schedule a retry."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    if isinstance(value, str) and len(value) > 64:
        return None
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def batch_provider_error(body: Any, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    """Keep bounded Google diagnostics, excluding arbitrary echoed payloads."""
    body = redact_secrets(redact_secret_values(body, secrets))
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return {"message": "Google batch request failed"}
    safe: dict[str, Any] = {
        key: value[:500]
        for key in ("message", "status")
        if isinstance(value := error.get(key), str)
    }
    if isinstance(error.get("code"), int):
        safe["code"] = error["code"]
    details = error.get("errors")
    if isinstance(details, list):
        safe["errors"] = [
            {
                key: value[:200]
                for key in ("reason", "domain", "message", "location", "locationType")
                if isinstance(value := item.get(key), str)
            }
            for item in details[:10]
            if isinstance(item, dict)
        ]
    return {"error": safe}


def parse_batch(
    response: httpx.Response,
    operations: list[str],
    parse_result: Callable[[int, Any, bool], dict[str, Any]],
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Parse only correlated HTTP parts; never return raw provider MIME/text."""
    items = [
        _unknown_item(index, op, "Missing batch response part")
        for index, op in enumerate(operations)
    ]
    protocol_issues: set[str] = set()
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + response.headers.get("content-type", "").encode("ascii")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + response.content
        )
        if message.get_content_type() != "multipart/mixed" or not message.is_multipart():
            raise ValueError
        if message.defects:
            # A missing closing boundary can still contain validated responses.
            protocol_issues.add("envelope_defect")
        seen: set[int] = set()
        for part in message.iter_parts():
            ids = part.get_all("Content-ID", [])
            match = (
                re.fullmatch(r"<response-item-(0|[1-9][0-9]*)>", str(ids[0]))
                if len(ids) == 1
                else None
            )
            if match is None or len(match[1]) > 4 or int(match[1]) >= len(items):
                protocol_issues.add("uncorrelated_part")
                continue
            index = int(match[1])
            if index in seen:
                protocol_issues.add("duplicate_part")
                items[index] = _unknown_item(
                    index, operations[index], "Duplicate batch response part"
                )
                continue
            seen.add(index)
            status = None
            try:
                if part.get_content_type() != "application/http" or part.defects:
                    raise ValueError
                raw = part.get_payload(decode=True)
                if not isinstance(raw, bytes):
                    raise ValueError
                line, rest = raw.split(b"\n", 1)
                status_match = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?\r?", line)
                if status_match is None:
                    raise ValueError
                status = int(status_match[1])
                nested = BytesParser(policy=policy.default).parsebytes(rest)
                if nested.defects or nested.is_multipart():
                    raise ValueError
                body = nested.get_payload(decode=True)
                if not isinstance(body, bytes):
                    raise ValueError
                try:
                    parsed = json.loads(body) if body and body.strip() else None
                except (ValueError, UnicodeError):
                    parsed = None
                item: dict[str, Any] = {
                    "index": index,
                    "operation": operations[index],
                    "status": status,
                    "provider_ids": _provider_ids(nested),
                }
                retry_after = batch_retry_after(nested.get("retry-after"))
                if retry_after is not None:
                    item["retry_after"] = retry_after
                if 200 <= status < 300:
                    item.update(
                        outcome="success",
                        result=parse_result(index, parsed, not (body or b"").strip()),
                    )
                else:
                    item.update(
                        outcome="unknown" if status >= 500 else "error",
                        provider_error=batch_provider_error(parsed, secrets),
                    )
                    if retry_after is not None:
                        item["provider_error"]["retry_after"] = retry_after
                items[index] = item
            except (ValueError, TypeError, UnicodeError):
                items[index] = _unknown_item(
                    index, operations[index], "Malformed batch response part", status
                )
    except (ValueError, TypeError, UnicodeError):
        # Preserve already validated receipts; unproven requests remain unknown.
        protocol_issues.add("malformed_envelope")
    summary = {**batch_summary(items), "provider_ids": _provider_ids(response.headers)}
    if protocol_issues:
        summary["protocol_error"] = {
            "message": "Google batch response has protocol errors",
            "issues": sorted(protocol_issues),
        }
        summary["reconcile_before_retry"] = True
    return summary
