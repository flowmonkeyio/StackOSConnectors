"""One fixed TDLib request per named action, using a caller-bound native session."""

from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from typing import Any

from stackos_connectors.contracts import (
    ConnectorFile,
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ConnectorError

from .schema import METHOD_ACTIONS
from .tdlib.native import (
    TelegramTdlibClosedError,
    TelegramTdlibNativeError,
    TelegramTdlibRequestError,
    safe_error_metadata,
)

_SENDS = frozenset({"sendMessage", "sendMessageAlbum", "forwardMessages"})


def _local_files(value: Any):
    if isinstance(value, dict):
        if value.get("@type") == "inputFileLocal":
            yield value.get("path")
        for item in value.values():
            yield from _local_files(item)
    elif isinstance(value, list):
        for item in value:
            yield from _local_files(item)


def _send_failure(update: dict[str, Any], temporary_id: int) -> dict[str, Any]:
    state = (update.get("message") or {}).get("sending_state") or {}
    error = update.get("error") or state.get("error") or {}
    code = error.get("code")
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    name, parsed_delay = safe_error_metadata(error.get("message"))
    state_delay = state.get("retry_after")
    delay = parsed_delay
    if (
        isinstance(state_delay, (int, float))
        and not isinstance(state_delay, bool)
        and math.isfinite(state_delay)
        and 0 < state_delay <= 7 * 24 * 60 * 60
    ):
        delay = max(parsed_delay or 0, state_delay)
    return {
        "temporary_message_id": temporary_id,
        "error_code": code,
        "provider_error": name,
        "retry_after_seconds": delay,
        "tdlib_can_retry": state.get("can_retry") is True,
        "tdlib_need_another_sender": state.get("need_another_sender") is True,
    }


class TelegramActionConnector:
    key = "telegram"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.options.native_session is None:
            return [
                ValidationIssue(
                    path="options.native_session",
                    message="An authorized caller-bound native session is required",
                )
            ]
        issues = []
        for value in _local_files(dict(request.input_json)):
            if (
                not isinstance(value, str)
                or not Path(value).is_absolute()
                or not Path(value).is_file()
            ):
                issues.append(
                    ValidationIssue(
                        path="data",
                        message="Native local input files must be explicit absolute file paths",
                    )
                )
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        session = request.options.native_session
        assert session is not None
        if request.operation not in METHOD_ACTIONS:
            raise ConnectorError(
                "Unsupported fixed Telegram method", metadata_json={"provider_executed": False}
            )
        data = dict(request.input_json)
        query = {"@type": request.operation, **data}
        if "@type" in data or "@extra" in data:
            raise ConnectorError(
                "Native route and correlation overrides are not accepted",
                metadata_json={"provider_executed": False},
            )
        try:
            result = await session.request(query, timeout=request.options.timeout or 30.0)
        except TelegramTdlibRequestError as exc:
            raise ConnectorError(
                str(exc),
                provider_status_code=exc.code,
                provider_error=exc.error_name,
                metadata_json={
                    "provider_executed": True,
                    "provider_result_known": True,
                    "retry_safe": exc.retry_after_seconds is not None,
                    "outcome_unknown": False,
                    "native_error_type": "request_rejected",
                    "phase": exc.phase,
                    "retry_after_seconds": exc.retry_after_seconds,
                },
            ) from None
        except Exception as exc:
            closed = isinstance(exc, TelegramTdlibClosedError)
            no_dispatch = closed and exc.request_dispatched is False
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, TelegramTdlibNativeError)
                and str(exc) == "TDLib request timed out awaiting a response."
            )
            raise ConnectorError(
                "TDLib request did not return a result",
                metadata_json={
                    "provider_executed": not no_dispatch,
                    "provider_result_known": False,
                    "retry_safe": False,
                    "outcome_unknown": not no_dispatch,
                    "correlation_id": request.options.correlation_id,
                    "native_error_type": "closed"
                    if no_dispatch
                    else "closed_after_dispatch"
                    if closed
                    else "timeout"
                    if timed_out
                    else "request_failed",
                },
            ) from None
        if not isinstance(result, dict):
            raise ConnectorError(
                "TDLib returned an unreadable native result",
                metadata_json={
                    "provider_executed": True,
                    "retry_safe": False,
                    "outcome_unknown": True,
                },
            )
        if request.operation in _SENDS:
            result = await self._join_receipts(request, result)
        files = []
        if request.operation == "downloadFile":
            local = result.get("local") or {}
            if not local.get("is_downloading_completed"):
                raise ConnectorError(
                    "Telegram file download did not complete",
                    metadata_json={
                        "provider_executed": True,
                        "retry_safe": True,
                        "outcome_unknown": False,
                        "file_id": data["file_id"],
                    },
                )
            source = Path(str(local.get("path") or "")).resolve()
            directory = Path(session.files_directory).resolve()
            if not source.is_relative_to(directory) or not source.is_file():
                raise ConnectorError(
                    "Telegram returned a file outside the bound session storage",
                    metadata_json={
                        "provider_executed": True,
                        "retry_safe": True,
                        "outcome_unknown": False,
                        "file_id": data["file_id"],
                    },
                )
            files.append(
                ConnectorFile(
                    path=str(source),
                    mime_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                    size_bytes=source.stat().st_size,
                )
            )
        return ConnectorResult(
            output_json=result,
            metadata_json={"transport": "tdlib", "provider_executed": True},
            files=files,
        )

    async def _join_receipts(
        self, request: ConnectorRequest, result: dict[str, Any]
    ) -> dict[str, Any]:
        session = request.options.native_session
        assert session is not None
        messages = result.get("messages", []) if result.get("@type") == "messages" else [result]
        chat_id = request.input_json["chat_id"]
        temporary_ids = [
            message["id"] for message in messages if message and message.get("sending_state")
        ]
        confirmed: list[dict[str, int]] = []
        final_messages = []
        failures = []

        def report(event):
            if request.options.progress_callback is not None:
                request.options.progress_callback(event)

        try:
            report(
                {
                    "phase": "provider_accepted",
                    "chat_id": chat_id,
                    "temporary_message_ids": temporary_ids,
                }
            )
            for message in messages:
                if message is None:
                    failures.append({"reason": "message_not_forwardable"})
                    continue
                temporary_id = None
                if message.get("sending_state"):
                    temporary_id = message["id"]
                    update = await session.wait_message(
                        chat_id, temporary_id, timeout=request.options.timeout or 60.0
                    )
                    if update.get("@type") == "updateMessageSendFailed":
                        failure = _send_failure(update, temporary_id)
                        failures.append(failure)
                        report({"phase": "message_failed", "chat_id": chat_id, "failure": failure})
                        continue
                    if update.get("@type") != "updateMessageSendSucceeded" or not isinstance(
                        update.get("message"), dict
                    ):
                        raise ValueError("unrecognized final message receipt")
                    message = update["message"]
                if (
                    not isinstance(message.get("id"), int)
                    or message.get("chat_id", chat_id) != chat_id
                ):
                    raise ValueError("mismatched final message receipt")
                confirmed.append({"chat_id": chat_id, "message_id": message["id"]})
                final_messages.append(message)
                report(
                    {
                        "phase": "message_confirmed",
                        "chat_id": chat_id,
                        "temporary_message_id": temporary_id,
                        "message": message,
                    }
                )
        except Exception:
            terminal_known = bool(messages) and len(confirmed) + len(failures) == len(messages)
            raise ConnectorError(
                "Telegram accepted the submission but final receipt processing did not complete",
                metadata_json={
                    "provider_executed": True,
                    "retry_safe": False,
                    "outcome_unknown": not terminal_known,
                    "provider_result_known": terminal_known,
                    "receipt_processing_failed": True,
                    "chat_id": chat_id,
                    "temporary_message_ids": temporary_ids,
                    "confirmed_messages": confirmed,
                    "failures": failures,
                },
            ) from None
        output = {
            "@type": "messages",
            "messages": final_messages,
            "temporary_message_ids": temporary_ids,
            "send_failures": failures,
        }
        if failures:
            raise ConnectorError(
                "Telegram could not complete every message",
                output_json=output,
                metadata_json={
                    "provider_executed": True,
                    "retry_safe": False,
                    "outcome_unknown": False,
                },
            )
        return output
