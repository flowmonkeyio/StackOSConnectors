"""IMAP action connector.

Official docs verified:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
- Python imaplib adapter: https://docs.python.org/3/library/imaplib.html
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import imaplib
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackos_connectors.connectors.imap.integration import imap_ssl_context
from stackos_connectors.contracts import (
    ConnectorFile,
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared.provider_utils import (
    credential_config,
    credential_payload,
    credential_value,
    issue,
    unknown_operation,
)

_TLS_MODES = {"ssl", "starttls", "none"}
_MAX_UIDVALIDITY = 4_294_967_295
_MESSAGE_FIELDS = {
    "subject",
    "from",
    "to",
    "cc",
    "date",
    "message_id",
    "text_preview",
    "html_preview",
    "body_text",
    "body_html",
    "flags",
    "headers",
}
_TEXT_CRITERIA = {"from", "to", "subject", "text"}
_DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")


class ImapActionConnector:
    key = "imap"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues = []
        if request.operation == "mailbox.list":
            return issues
        if request.operation not in {
            "messages.search",
            "message.fetch",
            "message.export",
            "message.mark_seen",
            "message.mark_unseen",
        }:
            return unknown_operation(request)
        _text(payload, "mailbox", issues, required=True)
        if request.operation == "messages.search":
            _optional_int(payload, "limit", issues, minimum=1, required=True)
            _optional_int(payload, "after_uid", issues, minimum=1, maximum=_MAX_UIDVALIDITY)
            _optional_uidvalidity(payload, issues)
            if payload.get("after_uid") is not None and not payload.get("expected_uidvalidity"):
                issues.append(
                    issue(
                        "$.expected_uidvalidity",
                        "after_uid requires expected_uidvalidity",
                        "required",
                    )
                )
            _criteria(payload.get("criteria"), issues)
        else:
            _optional_int(
                payload, "uid", issues, minimum=1, maximum=_MAX_UIDVALIDITY, required=True
            )
        if request.operation == "message.fetch":
            _fields(payload.get("fields"), issues)
            _optional_int(payload, "max_body_bytes", issues, minimum=1, required=True)
            _optional_int(payload, "preview_chars", issues, minimum=1, required=True)
        if request.operation.startswith("message.mark_"):
            _optional_uidvalidity(payload, issues)
        if request.operation == "message.export":
            limits = payload.get("limits")
            for key in (
                "message_bytes",
                "attachments",
                "attachment_bytes",
                "attachment_total_bytes",
                "mime_parts",
                "mime_depth",
            ):
                if (
                    not isinstance(limits, Mapping)
                    or not isinstance(limits.get(key), int)
                    or isinstance(limits.get(key), bool)
                    or limits[key] <= 0
                ):
                    issues.append(
                        issue("$.limits." + key, "must be a positive explicit integer", "required")
                    )
        return issues

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation == "message.export" and request.options.output_dir is None:
            raise ValidationError("IMAP message.export requires a caller-owned output directory")
        match request.operation:
            case "mailbox.list":
                return await asyncio.to_thread(_list_mailboxes, request)
            case "messages.search":
                return await asyncio.to_thread(_search_messages, request)
            case "message.fetch":
                return await asyncio.to_thread(_fetch_message, request)
            case "message.export":
                return await asyncio.to_thread(_export_message, request)
            case "message.mark_seen":
                return await asyncio.to_thread(_mark_message, request, seen=True)
            case "message.mark_unseen":
                return await asyncio.to_thread(_mark_message, request, seen=False)
            case _:
                raise ValidationError("unsupported IMAP operation")


def _mailbox_name(request: ConnectorRequest, settings: Mapping[str, Any]) -> str:
    return _validated_mailbox_name(request.input_json["mailbox"])


def _assert_contained(root: Path, target: Path) -> None:
    if target.is_symlink() or root.resolve() not in target.resolve().parents:
        raise _export_error(
            "unsafe_output", "IMAP output path must remain beneath caller directory"
        )


def _export_message(request: ConnectorRequest) -> ConnectorResult:
    """Fetch exact MIME and write plain files into the explicit caller directory."""
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    limits = request.input_json["limits"]
    directory = Path(request.options.output_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ValidationError("IMAP output_dir must be an existing non-symlink directory")
    if any(directory.iterdir()):
        raise ValidationError("IMAP output_dir must be empty to preserve caller files")
    client = None
    attempted = False
    try:
        client = _login(settings)
        attempted = True
        selected = _select(client, mailbox, readonly=True)
        uidvalidity = _required_uidvalidity(selected.get("uidvalidity"))
        typ, preflight = client.uid("FETCH", str(uid), "(UID RFC822.SIZE)")
        _ensure_export_ok(typ)
        preflight_size, _ = _export_fetch_tuple(preflight, uid=uid, require_literal=False)
        if preflight_size > limits["message_bytes"]:
            raise _export_error(
                "oversize",
                "IMAP message exceeds caller byte limit",
                details={"size_bytes": preflight_size, "max_bytes": limits["message_bytes"]},
            )
        typ, fetched = client.uid("FETCH", str(uid), "(UID RFC822.SIZE BODY.PEEK[])")
        _ensure_export_ok(typ)
        fetched_size, raw = _export_fetch_tuple(fetched, uid=uid, require_literal=True)
        assert raw is not None
        if fetched_size != preflight_size or len(raw) != preflight_size:
            raise _export_error(
                "size_mismatch",
                "IMAP export did not return the preflighted message size",
                details={"expected_bytes": preflight_size, "received_bytes": len(raw)},
            )
        attachments = _export_attachments(raw, limits)
        raw_manifest = _stage_export_file(directory, "original.eml", raw)
        attachment_manifest = [
            {
                "ordinal": ordinal,
                "path": f"attachment-{ordinal:03d}",
                "media_type": media_type,
                **_stage_export_file(directory, f"attachment-{ordinal:03d}", content),
            }
            for ordinal, (media_type, content) in enumerate(attachments, start=1)
        ]
        manifests = [
            {"path": "original.eml", "media_type": "message/rfc822", **raw_manifest},
            *attachment_manifest,
        ]
        return ConnectorResult(
            output_json={
                "provider": "imap",
                "operation": request.operation,
                "status": "success",
                "mailbox_name": mailbox,
                "uid": uid,
                "uidvalidity": uidvalidity,
                "content_sha256": raw_manifest["sha256"],
                "raw_mime": {"path": "original.eml", **raw_manifest},
                "attachments": attachment_manifest,
                "attachment_count": len(attachment_manifest),
                "attachment_total_bytes": sum(item["bytes"] for item in attachment_manifest),
            },
            metadata_json={
                "vendor": "imap",
                "operation": request.operation,
                "tls_mode": settings["tls_mode"],
                "provider_executed": True,
                "retry_safe": True,
            },
            files=[
                ConnectorFile(
                    path=str(directory / item["path"]),
                    name=item["path"],
                    mime_type=item["media_type"],
                    size_bytes=item["bytes"],
                )
                for item in manifests
            ],
        )
    except ConnectorError as exc:
        exc.metadata_json.update(
            {
                "provider_executed": attempted,
                "retry_safe": True,
                "tls_mode": settings["tls_mode"],
                "operation": request.operation,
            }
        )
        raise
    except Exception as exc:
        error = _export_error(
            "export_failed",
            "IMAP export could not complete safely",
            details={"error_category": type(exc).__name__},
        )
        error.metadata_json.update(
            {
                "provider_executed": attempted,
                "retry_safe": True,
                "tls_mode": settings["tls_mode"],
                "operation": request.operation,
            }
        )
        raise error from exc
    finally:
        if client is not None:
            _logout(client)


def _list_mailboxes(request: ConnectorRequest) -> ConnectorResult:
    settings = _imap_settings(request)
    client = _login(settings)
    try:
        # IMAP LIST command: https://www.rfc-editor.org/rfc/rfc9051.html#name-list-command
        typ, data = client.list()
        _ensure_ok(typ, "LIST")
        mailboxes = [_parse_list_line(line) for line in data or [] if line]
        return _connector_result(
            request,
            {
                "mailboxes": mailboxes,
                "mailbox_count": len(mailboxes),
            },
            settings,
        )
    finally:
        _logout(client)


def _search_messages(request: ConnectorRequest) -> ConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    limit = int(request.input_json["limit"])
    client = _login(settings)
    try:
        readonly_select = _select(client, mailbox, readonly=True)
        after_uid = request.input_json.get("after_uid")
        expected_uidvalidity = request.input_json.get("expected_uidvalidity")
        if after_uid is not None and expected_uidvalidity is None:
            raise ValidationError("IMAP search continuation requires expected_uidvalidity")
        if expected_uidvalidity is not None:
            actual_uidvalidity = _required_uidvalidity(readonly_select.get("uidvalidity"))
            if actual_uidvalidity != str(expected_uidvalidity):
                raise ValidationError(
                    "IMAP UIDVALIDITY changed; restart search without after_uid and "
                    "reconcile message identities before continuing"
                )
        raw_criteria = request.input_json.get("criteria") or {}
        criteria = _search_criteria(raw_criteria)
        lower = max(int(raw_criteria.get("uid_from") or 1), int(after_uid or 0) + 1)
        uid_to = raw_criteria.get("uid_to")
        upper = _MAX_UIDVALIDITY if uid_to is None or uid_to == "*" else int(uid_to)
        if after_uid is not None and lower <= upper:
            criteria.extend(["UID", f"{lower}:*"])
        # IMAP UID SEARCH command:
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-uid-command
        # n:* can return the highest UID even when it is below n. Filter the
        # intended bounds locally, and never send a UID beyond the 32-bit range.
        matched_uids: list[int] = []
        if lower <= upper:
            typ, data = client.uid("SEARCH", None, *criteria)
            _ensure_ok(typ, "UID SEARCH")
            matched_uids = sorted({uid for uid in _uid_list(data) if lower <= uid <= upper})
        uids = matched_uids[:limit]
        has_more = len(matched_uids) > len(uids)
        result = {
            "mailbox_name": mailbox,
            "uidvalidity": readonly_select.get("uidvalidity"),
            "criteria": criteria,
            "uids": uids,
            "count": len(uids),
            "matched_count": len(matched_uids),
            "has_more": has_more,
            "next_after_uid": uids[-1] if has_more else None,
            "limit": limit,
        }
        return _connector_result(request, result, settings)
    except ConnectorError as exc:
        exc.metadata_json["provider_executed"] = True
        exc.metadata_json.setdefault("retry_safe", True)
        raise
    finally:
        _logout(client)


def _fetch_message(request: ConnectorRequest) -> ConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    fields = _requested_fields(request.input_json.get("fields"))
    max_body_bytes = int(request.input_json["max_body_bytes"])
    client = _login(settings)
    try:
        select_data = _select(client, mailbox, readonly=True)
        # Use UID FETCH and BODY.PEEK so reads do not mutate \\Seen.
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-fetch-command
        typ, data = client.uid(
            "FETCH",
            str(uid),
            f"(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.{max_body_bytes}>)",
        )
        _ensure_ok(typ, "UID FETCH")
        raw, flags, size = _fetch_payload(data, uid=uid)
        if raw is None:
            raise ValidationError(f"IMAP message UID {uid} was not found")
        if size is not None and size < len(raw):
            raise ValidationError("IMAP fetch returned more message bytes than RFC822.SIZE")
        bounded_raw = raw[:max_body_bytes]
        parsed = BytesParser(policy=policy.default).parsebytes(bounded_raw)
        message = _message_output(
            parsed,
            fields=fields,
            mailbox=mailbox,
            uid=uid,
            uidvalidity=select_data.get("uidvalidity"),
            flags=flags,
            size=size,
            max_body_bytes=max_body_bytes,
            fetched_bytes=len(raw),
            parsed_bytes=len(bounded_raw),
            preview_chars=request.input_json["preview_chars"],
        )
        return _connector_result(request, message, settings)
    except ConnectorError as exc:
        exc.metadata_json["provider_executed"] = True
        exc.metadata_json.setdefault("retry_safe", True)
        raise
    finally:
        _logout(client)


def _mark_message(
    request: ConnectorRequest,
    *,
    seen: bool,
) -> ConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    expected_uidvalidity = request.input_json.get("expected_uidvalidity")
    client = _login(settings)
    try:
        selected = _select(client, mailbox, readonly=False)
        selected_uidvalidity = selected.get("uidvalidity")
        if expected_uidvalidity is not None:
            actual_uidvalidity = _required_uidvalidity(selected_uidvalidity)
            if actual_uidvalidity != str(expected_uidvalidity):
                raise ValidationError("IMAP UIDVALIDITY no longer matches the selected mailbox")
        op = "+FLAGS" if seen else "-FLAGS"
        # IMAP STORE command for \\Seen lifecycle:
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-store-command
        # A tagged OK also permits a nonexistent UID (RFC 9051 section 6.4.9).
        # Observe the exact UID's flags before claiming acknowledgement.
        try:
            typ, _data = client.uid("STORE", str(uid), op, "(\\Seen)")
            _ensure_ok(typ, f"UID STORE {op}")
            typ, data = client.uid("FETCH", str(uid), "(UID FLAGS)")
            _ensure_ok(typ, "UID FETCH flags")
            matches = []
            for item in data or []:
                if not isinstance(item, bytes):
                    continue
                metadata = _safe_decode(item)
                if _single_fetch_number(metadata, r"\bUID\s+(\d+)") == uid and re.search(
                    r"\bFLAGS\s+\([^)]*\)", metadata, re.IGNORECASE
                ):
                    matches.append(_parse_flags(metadata))
            if len(matches) != 1 or (("\\Seen" in matches[0]) != seen):
                raise ValidationError("IMAP flag readback did not confirm the requested UID state")
        except Exception as exc:
            # Preserve the selected identity even when the caller did not supply
            # an expected epoch. Never echo arbitrary server response text.
            try:
                observed_uidvalidity = _required_uidvalidity(selected_uidvalidity)
            except ConnectorError:
                observed_uidvalidity = None
            provider_error = {
                "outcome_unknown": True,
                "retry_safe": False,
                "recovery": "Re-read the exact mailbox epoch, UID and flags before "
                "retrying the flag write.",
            }
            raise ConnectorError(
                "IMAP flag write outcome requires reconciliation",
                output_json={
                    "status": "failed",
                    "provider_error": provider_error,
                    "mailbox_name": mailbox,
                    "uid": uid,
                    "uidvalidity": observed_uidvalidity,
                    "requested_seen": seen,
                    "store_applied": None,
                    "store_confirmed": False,
                },
                metadata_json={
                    "provider_executed": True,
                    "outcome_unknown": True,
                    "retry_safe": False,
                },
                provider_error=provider_error,
            ) from exc
        result = {
            "mailbox_name": mailbox,
            "uid": uid,
            "uidvalidity": selected_uidvalidity,
            "seen": seen,
            "store_applied": True,
        }
        return _connector_result(request, result, settings)
    except ConnectorError as exc:
        exc.metadata_json["provider_executed"] = True
        exc.metadata_json.setdefault("retry_safe", True)
        raise
    finally:
        _logout(client)


def _imap_settings(request: ConnectorRequest) -> dict[str, Any]:
    config = credential_config(request)
    payload = credential_payload(request)
    host = _config_text(config, payload, "host", required=True)
    username = _config_text(config, payload, "username", "user", required=True)
    port = _config_int(config, payload, "port", default=993)
    tls_mode = _config_text(config, payload, "tls_mode", default="ssl").lower()
    if tls_mode not in _TLS_MODES:
        raise ValidationError("imap credential tls_mode must be ssl, starttls, or none")
    return {
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "tls_ca_pem": config.get("tls_ca_pem"),
        "username": username,
        "password": credential_value(request, "password", "secret"),
        "timeout_s": request.options.timeout
        if request.options.timeout is not None
        else float(_config_int(config, payload, "timeout_s", default=30)),
    }


def _login(settings: Mapping[str, Any]) -> Any:
    host = str(settings["host"])
    port = int(settings["port"])
    timeout = float(settings["timeout_s"])
    tls_context = imap_ssl_context(settings.get("tls_ca_pem"))
    if settings["tls_mode"] == "ssl":
        client: Any = imaplib.IMAP4_SSL(
            host,
            port,
            ssl_context=tls_context,
            timeout=timeout,
        )
    else:
        client = imaplib.IMAP4(host, port, timeout=timeout)
    try:
        if settings["tls_mode"] == "starttls":
            client.starttls(ssl_context=tls_context)
        client.login(str(settings["username"]), str(settings["password"]))
    except Exception:
        _logout(client)
        raise
    return client


def _logout(client: Any) -> None:
    # LOGOUT releases selection without expunging pre-existing \\Deleted mail.
    # CLOSE would delete unrelated messages after a writable flag action.
    # https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.1
    with suppress(Exception):
        client.logout()


def _select(client: Any, mailbox: str, *, readonly: bool) -> dict[str, Any]:
    typ, _data = client.select(mailbox, readonly=readonly)
    _ensure_ok(typ, "SELECT")
    uidvalidity = None
    try:
        response = client.response("UIDVALIDITY")
    except Exception:
        response = None
    if isinstance(response, tuple) and len(response) == 2:
        values = response[1]
        if isinstance(values, list) and values:
            uidvalidity = _safe_decode(values[0])
    return {"uidvalidity": uidvalidity}


def _validated_mailbox_name(value: str) -> str:
    mailbox = str(value).strip()
    if not mailbox or _has_crlf(mailbox):
        raise ValidationError("IMAP mailbox reference resolved to an invalid mailbox name")
    return mailbox


def _search_criteria(raw: Any) -> list[str]:
    if raw is None:
        return ["ALL"]
    if not isinstance(raw, Mapping):
        raise ValidationError("criteria must be an object")
    criteria: list[str] = []
    if raw.get("unseen") is True:
        criteria.append("UNSEEN")
    if raw.get("seen") is True:
        criteria.append("SEEN")
    for key in ("since", "before"):
        value = raw.get(key)
        if value is not None:
            text = str(value).strip()
            if not _DATE_RE.match(text):
                raise ValidationError(f"criteria.{key} must use IMAP date format DD-Mon-YYYY")
            criteria.extend([key.upper(), text])
    for key in _TEXT_CRITERIA:
        value = raw.get(key)
        if value is not None:
            text = str(value).strip()
            if not text or _has_crlf(text):
                raise ValidationError(f"criteria.{key} must be non-empty text without CR/LF")
            criteria.extend([key.upper() if key != "text" else "TEXT", text])
    uid_from = raw.get("uid_from")
    uid_to = raw.get("uid_to")
    if uid_from is not None or uid_to is not None:
        start = _positive_int(uid_from or 1, "criteria.uid_from")
        end = _positive_int(uid_to or "*", "criteria.uid_to", allow_star=True)
        criteria.extend(["UID", f"{start}:{end}"])
    return criteria or ["ALL"]


def _uid_list(data: Sequence[Any] | None) -> list[int]:
    if not data:
        return []
    text = " ".join(_safe_decode(item) for item in data if item)
    out: list[int] = []
    for part in text.split():
        if part.isdigit():
            out.append(int(part))
    return out


def _fetch_payload(
    data: Sequence[Any] | None, *, uid: int
) -> tuple[bytes | None, list[str], int | None]:
    if not data:
        return None, [], None
    matches: list[tuple[bytes, list[str], int | None]] = []
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            meta = _safe_decode(item[0])
            if _single_fetch_number(meta, r"\bUID\s+(\d+)") != uid:
                raise ValidationError("IMAP fetch returned a mismatched or missing message UID")
            if not isinstance(item[1], bytes):
                raise ValidationError("IMAP fetch returned an invalid message literal")
            matches.append((item[1], sorted(set(_parse_flags(meta))), _parse_size(meta)))
    if len(matches) > 1:
        raise ValidationError("IMAP fetch returned ambiguous message literals")
    return matches[0] if matches else (None, [], None)


def _export_fetch_tuple(
    data: Sequence[Any] | None,
    *,
    uid: int,
    require_literal: bool,
) -> tuple[int, bytes | None]:
    """Bind UID, RFC822.SIZE, and an optional literal from one FETCH response."""
    matches: list[tuple[int, bytes | None]] = []
    for item in data or []:
        literal: bytes | None
        if isinstance(item, tuple) and len(item) >= 2:
            metadata = _safe_decode(item[0])
            literal = item[1] if isinstance(item[1], bytes) else None
        elif isinstance(item, bytes):
            # imaplib returns metadata-only FETCH responses (for example,
            # RFC822.SIZE preflight) as a bare bytes item. Literal-bearing
            # FETCH responses use a (metadata, literal) tuple.
            metadata = _safe_decode(item)
            literal = None
        else:
            continue
        response_uid = _single_fetch_number(metadata, r"\bUID\s+(\d+)")
        if response_uid is None:
            continue
        if response_uid != uid:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence export returned a different message UID"
            )
        size = _single_fetch_number(metadata, r"\bRFC822\.SIZE\s+(\d+)")
        if size is None:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence export did not return a usable message size"
            )
        if require_literal:
            literal_size = _single_fetch_number(metadata, r"\bBODY(?:\.PEEK)?\[\]\s+\{(\d+)\}")
            if literal is None or literal_size is None or literal_size != len(literal):
                raise _export_error(
                    "fetch_truncated",
                    "IMAP evidence export did not return a complete message literal",
                )
        elif literal not in {None, b""}:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence preflight unexpectedly returned message content"
            )
        matches.append((size, literal))
    if len(matches) != 1:
        raise _export_error(
            "fetch_ambiguous", "IMAP evidence export did not return exactly one matching message"
        )
    return matches[0]


def _single_fetch_number(metadata: str, expression: str) -> int | None:
    values = re.findall(expression, metadata, flags=re.IGNORECASE)
    if len(values) != 1:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _required_uidvalidity(value: Any) -> str:
    if isinstance(value, bool):
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    text = str(value or "").strip()
    if not text.isascii() or not text.isdigit():
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    numeric = int(text)
    if numeric < 1 or numeric > _MAX_UIDVALIDITY:
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    return str(numeric)


def _export_attachments(raw: bytes, limits: Mapping[str, int]) -> list[tuple[str, bytes]]:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise _export_error("malformed", "IMAP evidence MIME is malformed") from exc
    if message.defects:
        raise _export_error("malformed", "IMAP evidence MIME is malformed")

    parts = _bounded_mime_parts(message, limits)
    attachments: list[tuple[str, bytes]] = []
    attachment_paths: list[tuple[int, ...]] = []
    attachment_total = 0
    for part, path in parts:
        if any(path[: len(parent)] == parent for parent in attachment_paths):
            continue
        if not _is_attachment(part):
            continue
        if len(attachments) >= limits["attachments"]:
            raise _export_error(
                "attachment_count_exceeded",
                "IMAP evidence export exceeds the attachment count limit",
                details={"max_attachments": limits["attachments"]},
            )
        payload = _attachment_bytes(part)
        if len(payload) > limits["attachment_bytes"]:
            raise _export_error(
                "attachment_oversize",
                "IMAP evidence export contains an attachment above the caller byte limit",
                details={"max_attachment_bytes": limits["attachment_bytes"]},
            )
        attachment_total += len(payload)
        if attachment_total > limits["attachment_total_bytes"]:
            raise _export_error(
                "attachment_total_oversize",
                "IMAP evidence export exceeds the attachment aggregate limit",
                details={"max_attachment_total_bytes": limits["attachment_total_bytes"]},
            )
        attachments.append((_safe_media_type(part.get_content_type()), payload))
        attachment_paths.append(path)
    return attachments


def _bounded_mime_parts(
    message: Message, limits: Mapping[str, int]
) -> list[tuple[Message, tuple[int, ...]]]:
    stack: list[tuple[Message, int, tuple[int, ...]]] = [(message, 1, ())]
    collected: list[tuple[Message, tuple[int, ...]]] = []
    part_count = 0
    while stack:
        part, depth, path = stack.pop()
        part_count += 1
        if part_count > limits["mime_parts"] or depth > limits["mime_depth"]:
            raise _export_error(
                "mime_structure_exceeded", "IMAP evidence MIME structure exceeds limits"
            )
        if part.defects:
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
        collected.append((part, path))
        if not part.is_multipart():
            continue
        children = part.get_payload()
        if not isinstance(children, list):
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
        stack.extend(
            (child, depth + 1, (*path, index))
            for index, child in reversed(list(enumerate(children)))
            if isinstance(child, Message)
        )
        if len(children) != sum(isinstance(child, Message) for child in children):
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
    return collected


def _is_attachment(part: Message) -> bool:
    return part.get_content_disposition() == "attachment" or part.get_filename() is not None


def _attachment_bytes(part: Message) -> bytes:
    if part.is_multipart() or part.get_content_type().lower() == "message/rfc822":
        return part.as_bytes(policy=policy.default)
    encoding = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
    if encoding not in {"", "7bit", "8bit", "binary", "base64", "quoted-printable"}:
        raise _export_error(
            "unsupported_encoding", "IMAP evidence attachment encoding is unsupported"
        )
    if encoding == "base64":
        encoded = part.get_payload()
        if not isinstance(encoded, str):
            raise _export_error("malformed", "IMAP evidence attachment is malformed")
        try:
            return base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _export_error("malformed", "IMAP evidence attachment is malformed") from exc
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if raw_payload is None or raw_payload == "":
            return b""
        raise _export_error("malformed", "IMAP evidence attachment is malformed")
    if not isinstance(payload, bytes):
        raise _export_error("malformed", "IMAP evidence attachment is malformed")
    return payload


def _safe_media_type(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if len(candidate) <= 127 and re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", candidate):
        return candidate
    return "application/octet-stream"


def _stage_export_file(directory: Path, name: str, payload: bytes) -> dict[str, Any]:
    target = directory / name
    _assert_contained(directory, target)
    temporary = directory / f".{name}.{uuid4().hex}.tmp"
    _assert_contained(directory, temporary)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, target)
        staged = target.read_bytes()
    except OSError as exc:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise _export_error(
            "staging_unavailable", "IMAP evidence staging could not be written safely"
        ) from exc
    if staged != payload:
        raise _export_error("staging_mismatch", "IMAP evidence staging verification failed")
    return {"bytes": len(staged), "sha256": hashlib.sha256(staged).hexdigest()}


def _ensure_export_ok(status: Any) -> None:
    if str(status).upper() != "OK":
        raise _export_error("provider_rejected", "IMAP evidence export request was not accepted")


def _export_error(
    category: str,
    detail: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> ConnectorError:
    output: dict[str, Any] = {"status": "rejected", "category": category}
    if details:
        output.update(dict(details))
    return ConnectorError(
        detail,
        output_json=output,
        metadata_json={"vendor": "imap", "category": category},
    )


def _message_output(
    message: Message,
    *,
    fields: set[str],
    mailbox: str,
    uid: int,
    uidvalidity: str | None,
    flags: list[str],
    size: int | None,
    max_body_bytes: int,
    fetched_bytes: int,
    parsed_bytes: int,
    preview_chars: int,
) -> dict[str, Any]:
    # One extra character detects local body clipping without an unbounded read.
    text_body, html_body = _message_bodies(message, max_body_bytes=max_body_bytes + 1)
    headers = {
        key: str(message.get(key) or "")
        for key in ("Subject", "From", "To", "Cc", "Date", "Message-ID")
        if message.get(key) is not None
    }
    base: dict[str, Any] = {
        "mailbox_name": mailbox,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "size_bytes": size,
    }
    candidates = {
        "subject": str(message.get("Subject") or ""),
        "from": _addresses(message.get_all("From", [])),
        "to": _addresses(message.get_all("To", [])),
        "cc": _addresses(message.get_all("Cc", [])),
        "date": str(message.get("Date") or ""),
        "message_id": str(message.get("Message-ID") or ""),
        "text_preview": text_body[:preview_chars],
        "html_preview": html_body[:preview_chars],
        "body_text": text_body[:max_body_bytes],
        "body_html": html_body[:max_body_bytes],
        "flags": flags,
        "headers": headers,
    }
    for key in fields:
        base[key] = candidates[key]
    field_limits = {
        "text_preview": (text_body, preview_chars),
        "html_preview": (html_body, preview_chars),
        "body_text": (text_body, max_body_bytes),
        "body_html": (html_body, max_body_bytes),
    }
    base["content_completeness"] = {
        "scope": "parsed_fields_from_mime_prefix",
        "fetched_bytes": fetched_bytes,
        "parsed_bytes": parsed_bytes,
        "max_body_bytes": max_body_bytes,
        "raw_message_complete": parsed_bytes == size if size is not None else None,
        "raw_message_truncated": parsed_bytes < size if size is not None else None,
        "truncated_fields": sorted(
            key
            for key, (text, limit) in field_limits.items()
            if key in fields and len(text) > limit
        ),
        "mime_parse_defects": any(part.defects for part in message.walk()),
    }
    return base


def _requested_fields(raw: Any) -> set[str]:
    if not isinstance(raw, list) or not raw:
        raise ValidationError("fields must be a non-empty array")
    fields = {str(item) for item in raw}
    invalid = fields - _MESSAGE_FIELDS
    if invalid:
        raise ValidationError(f"unsupported IMAP message fields: {', '.join(sorted(invalid))}")
    return fields


def _message_bodies(message: Message, *, max_body_bytes: int) -> tuple[str, str]:
    text = ""
    html = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and not text:
                text = _part_text(part, max_body_bytes=max_body_bytes)
            elif content_type == "text/html" and not html:
                html = _part_text(part, max_body_bytes=max_body_bytes)
    elif isinstance(message, EmailMessage):
        if message.get_content_type() == "text/html":
            html = _part_text(message, max_body_bytes=max_body_bytes)
        else:
            text = _part_text(message, max_body_bytes=max_body_bytes)
    else:
        payload = message.get_payload(decode=True)
        text = _safe_decode(payload)[:max_body_bytes] if payload else ""
    return text[:max_body_bytes], html[:max_body_bytes]


def _part_text(part: Message, *, max_body_bytes: int) -> str:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
        except Exception:
            raw = part.get_payload(decode=True)
            return _safe_decode(raw)[:max_body_bytes] if raw else ""
        if isinstance(content, bytes):
            return _safe_decode(content)[:max_body_bytes]
        return str(content)[:max_body_bytes]
    raw = part.get_payload(decode=True)
    return _safe_decode(raw)[:max_body_bytes] if raw else ""


def _addresses(values: Sequence[str]) -> list[str]:
    return [address for _name, address in getaddresses(values) if address]


def _parse_list_line(value: Any) -> dict[str, Any]:
    text = _safe_decode(value)
    flags = re.findall(r"\\[A-Za-z]+", text)
    mailbox = text.split(' "/" ')[-1].strip().strip('"') if ' "/" ' in text else text.split()[-1]
    return {
        "name": mailbox,
        "flags": flags,
    }


def _parse_flags(text: str) -> list[str]:
    match = re.search(r"FLAGS \(([^)]*)\)", text, flags=re.IGNORECASE)
    if match is None:
        return []
    return [part for part in match.group(1).split() if part]


def _parse_size(text: str) -> int | None:
    match = re.search(r"RFC822\.SIZE\s+(\d+)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _connector_result(
    request: ConnectorRequest,
    body: dict[str, Any],
    settings: Mapping[str, Any],
) -> ConnectorResult:
    return ConnectorResult(
        output_json={
            "provider": "imap",
            "operation": request.operation,
            "status": "success",
            **body,
        },
        metadata_json={
            "vendor": "imap",
            "operation": request.operation,
            "tls_mode": settings["tls_mode"],
            "provider_executed": True,
            "retry_safe": not request.operation.startswith("message.mark_"),
        },
    )


def _ensure_ok(status: Any, label: str) -> None:
    if str(status).upper() != "OK":
        raise ValidationError(f"IMAP {label} failed with status {status!r}")


def _config_text(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    required: bool = False,
) -> str:
    for source in (config, payload):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (dict, list)):
                text = str(value).strip()
                if text:
                    return text
    if required:
        raise ValidationError(f"imap credential missing {keys[0]}")
    return default or ""


def _config_int(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    raw = config.get(key, payload.get(key, default))
    if isinstance(raw, bool):
        raise ValidationError(f"imap credential {key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"imap credential {key} must be an integer") from exc
    if value < 1 or value > 65_535:
        raise ValidationError(f"imap credential {key} must be between 1 and 65535")
    return value


def _text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, str) or not value.strip() or _has_crlf(value):
        issues.append(issue(f"$.{key}", f"{key} must be text without CR/LF", "format"))


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int | None = None,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(issue(f"$.{key}", f"{key} must be an integer >= {minimum}", "range"))
        return
    if maximum is not None and value > maximum:
        issues.append(issue(f"$.{key}", f"{key} must be <= {maximum}", "range"))


def _optional_uidvalidity(
    payload: Mapping[str, Any],
    issues: list[ValidationIssue],
) -> None:
    value = payload.get("expected_uidvalidity")
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(
            issue(
                "$.expected_uidvalidity",
                "expected_uidvalidity must be a bounded nonzero numeric string",
                "format",
            )
        )
        return
    try:
        _required_uidvalidity(value)
    except ConnectorError:
        issues.append(
            issue(
                "$.expected_uidvalidity",
                "expected_uidvalidity must be a bounded nonzero numeric string",
                "format",
            )
        )


def _criteria(value: Any, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(issue("$.criteria", "criteria must be an object", "type_error"))
        return
    allowed = {"unseen", "seen", "since", "before", "uid_from", "uid_to", *_TEXT_CRITERIA}
    for key, item in value.items():
        if key not in allowed:
            issues.append(issue(f"$.criteria.{key}", f"unsupported criteria {key}", "forbidden"))
        elif key in {"unseen", "seen"}:
            if not isinstance(item, bool):
                issues.append(issue(f"$.criteria.{key}", f"{key} must be boolean", "type_error"))
        elif key in {"uid_from", "uid_to"}:
            if item != "*" and (not isinstance(item, int) or isinstance(item, bool) or item < 1):
                issues.append(issue(f"$.criteria.{key}", f"{key} must be a positive integer"))
        elif key in {"since", "before"}:
            if (
                not isinstance(item, str)
                or not item.strip()
                or _has_crlf(item)
                or not _DATE_RE.match(item.strip())
            ):
                issues.append(
                    issue(
                        f"$.criteria.{key}",
                        f"{key} must use IMAP date format DD-Mon-YYYY",
                    )
                )
        elif not isinstance(item, str) or not item.strip() or _has_crlf(item):
            issues.append(issue(f"$.criteria.{key}", f"{key} must be safe text"))


def _fields(value: Any, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.fields", "fields must be a non-empty array", "type_error"))
        return
    invalid = {
        str(item) for item in value if not isinstance(item, str) or item not in _MESSAGE_FIELDS
    }
    if invalid:
        issues.append(issue("$.fields", f"unsupported fields: {', '.join(sorted(invalid))}"))


def _positive_int(value: Any, label: str, *, allow_star: bool = False) -> int | str:
    if allow_star and value == "*":
        return "*"
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(f"{label} must be a positive integer")
    return value


def _safe_decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value
