"""Native SMTP submission; sender selection and history belong to the caller."""

from __future__ import annotations

import asyncio
import smtplib
from collections.abc import Mapping
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from typing import Any

from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared.provider_utils import (
    credential_config,
    credential_payload,
    credential_value,
    issue,
    unknown_operation,
)

_TLS_MODES = {"starttls", "ssl", "none"}
_DISALLOWED_HEADERS = {
    "bcc",
    "cc",
    "cookie",
    "from",
    "subject",
    "to",
    "authorization",
    "set-cookie",
}


class SmtpActionConnector:
    key = "smtp"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "email.send":
            return unknown_operation(request)
        payload = request.input_json
        issues = []
        for key in ("recipients", "cc", "bcc"):
            try:
                recipients = _email_list(payload.get(key), "$." + key)
                if key == "recipients" and not recipients:
                    issues.append(issue("$.recipients", "recipients must not be empty", "required"))
            except ValidationError:
                issues.append(issue("$." + key, "must contain valid email addresses", "format"))
        for key in ("from_email", "reply_to"):
            value = payload.get(key)
            if (key == "from_email" or value is not None) and (
                not isinstance(value, str) or not _is_email(value)
            ):
                issues.append(issue("$." + key, "must be a valid email address", "format"))
        if (
            not isinstance(payload.get("subject"), str)
            or not payload["subject"].strip()
            or _has_crlf(payload["subject"])
        ):
            issues.append(issue("$.subject", "subject must be nonempty without CR/LF", "format"))
        if not _has_nonempty_text(payload.get("text")) and not _has_nonempty_text(
            payload.get("html")
        ):
            issues.append(issue("$", "text or html is required", "required"))
        try:
            _clean_headers(payload.get("headers"))
        except ValidationError:
            issues.append(issue("$.headers", "headers contain invalid or managed fields", "format"))
        return issues

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        return await asyncio.to_thread(_send_email, request)


def _send_email(request: ConnectorRequest) -> ConnectorResult:
    settings = _smtp_settings(request)
    message, recipients, safe_from = _build_message(request, settings)

    # SMTP AUTH and mail transaction:
    # https://www.rfc-editor.org/rfc/rfc4954
    # https://www.rfc-editor.org/rfc/rfc5321.html#section-4.1
    refused: dict[str, tuple[int, bytes]] = {}
    client: Any | None = None
    attempted = False
    try:
        client = _smtp_client(settings)
        if settings["tls_mode"] == "starttls":
            client.ehlo()
            client.starttls()
            client.ehlo()
        client.login(settings["username"], settings["password"])
        attempted = True
        refused = client.send_message(
            message,
            from_addr=request.input_json["from_email"],
            to_addrs=recipients,
        )
    except smtplib.SMTPRecipientsRefused as exc:
        refused = exc.recipients
    except Exception as exc:
        raise ConnectorError(
            "SMTP submission failed" if not attempted else "SMTP submission outcome is unknown",
            provider_error={"type": type(exc).__name__},
            output_json={
                "provider": "smtp",
                "message_id": str(message["Message-ID"]),
                "status": "unknown" if attempted else "failed",
            },
            metadata_json={
                "provider_executed": attempted,
                "outcome_unknown": attempted,
                "retry_safe": not attempted,
            },
        ) from exc
    finally:
        if client is not None:
            with suppress(Exception):
                client.quit()
            with suppress(Exception):
                client.close()

    rejected = {
        address: {"smtp_code": code, "smtp_message": _decode_smtp_message(text)}
        for address, (code, text) in refused.items()
    }
    accepted = [address for address in recipients if address not in refused]
    status = "accepted" if not rejected else ("rejected" if not accepted else "partial")
    message_id = str(message["Message-ID"])
    return ConnectorResult(
        output_json={
            "provider": "smtp",
            "operation": request.operation,
            "status": status,
            "message_id": message_id,
            "from_email": safe_from,
            "recipient_count": len(recipients),
            "accepted_recipient_count": len(accepted),
            "rejected_recipient_count": len(rejected),
            "accepted_recipients": accepted,
            "rejected_recipients": rejected,
        },
        metadata_json={
            "provider_executed": True,
            "retry_safe": False,
            "vendor": "smtp",
            "operation": request.operation,
            "tls_mode": settings["tls_mode"],
            "host": settings["host"],
            "port": settings["port"],
        },
    )


def _smtp_settings(request: ConnectorRequest) -> dict[str, Any]:
    config = credential_config(request)
    payload = credential_payload(request)
    host = _config_text(config, payload, "host", required=True)
    username = _config_text(config, payload, "username", "user", required=True)
    port = _config_int(config, payload, "port", default=465)
    tls_mode = _config_text(config, payload, "tls_mode", default="ssl").lower()
    if tls_mode not in _TLS_MODES:
        raise ValidationError("smtp credential tls_mode must be starttls, ssl, or none")
    password = credential_value(request, "password", "secret")
    return {
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "username": username,
        "password": password,
        "timeout_s": request.options.timeout
        if request.options.timeout is not None
        else float(_config_int(config, payload, "timeout_s", default=30)),
    }


def _build_message(
    request: ConnectorRequest,
    settings: Mapping[str, Any],
) -> tuple[EmailMessage, list[str], str]:
    payload = request.input_json
    recipients = _email_list(payload.get("recipients"), "$.recipients")
    cc = _email_list(payload.get("cc"), "$.cc")
    bcc = _email_list(payload.get("bcc"), "$.bcc")
    all_recipients = [*recipients, *cc, *bcc]

    from_email = str(payload["from_email"])
    reply_to = str(payload.get("reply_to") or "")
    subject = str(payload["subject"]).strip()
    if _has_crlf(subject):
        raise ValidationError("SMTP subject cannot contain CR/LF")

    message = EmailMessage()
    message["Message-ID"] = make_msgid()
    message["From"] = formataddr((str(payload.get("from_name") or ""), from_email))
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    for name, value in _clean_headers(payload.get("headers")).items():
        message[name] = value

    text = str(payload.get("text") or "")
    html = str(payload.get("html") or "")
    if text and html:
        message.set_content(text)
        message.add_alternative(html, subtype="html")
    elif html:
        message.set_content(html, subtype="html")
    else:
        message.set_content(text)
    safe_from = from_email
    return message, all_recipients, safe_from


def _smtp_client(settings: Mapping[str, Any]) -> Any:
    host = str(settings["host"])
    port = int(settings["port"])
    timeout = float(settings["timeout_s"])
    try:
        if settings["tls_mode"] == "ssl":
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        return smtplib.SMTP(host, port, timeout=timeout)
    except TypeError:
        if settings["tls_mode"] == "ssl":
            return smtplib.SMTP_SSL(host, port)
        return smtplib.SMTP(host, port)


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
        raise ValidationError(f"smtp credential missing {keys[0]}")
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
        raise ValidationError(f"smtp credential {key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"smtp credential {key} must be an integer") from exc
    if value < 1 or value > 65_535:
        raise ValidationError(f"smtp credential {key} must be between 1 and 65535")
    return value


def _email_list(value: Any, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{path} must be an array")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _is_email(item):
            raise ValidationError(f"{path} contains an invalid email address")
        out.append(parseaddr(item)[1])
    return out


def _clean_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("headers must be an object")
    out: dict[str, str] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(item, str)
            or _has_crlf(key)
            or _has_crlf(item)
            or key.lower() in _DISALLOWED_HEADERS
        ):
            raise ValidationError("headers contain an invalid or managed header")
        out[key] = item
    return out


def _is_email(value: str) -> bool:
    if _has_crlf(value):
        return False
    _name, address = parseaddr(value)
    return bool(address and "@" in address and parseaddr(address)[1] == address)


def _has_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value


def _decode_smtp_message(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")[:500]
