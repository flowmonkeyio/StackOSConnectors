"""ElevenLabs REST protocol and subscription-backed credential verification."""

import os
import re
import stat
from math import isfinite
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError, ValidationError
from stackos_connectors.shared.base import BaseIntegration
from stackos_connectors.shared.media import decode_base64_media, write_media_file

_MODEL_FIELDS = (
    "model_id",
    "name",
    "can_do_text_to_speech",
    "can_use_style",
    "can_use_speaker_boost",
    "languages",
    "maximum_text_length_per_request",
)
_SETTINGS_FIELDS = (
    "stability",
    "similarity_boost",
    "style",
    "speed",
    "use_speaker_boost",
    "clone_strength",
)
_DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
_AUDIO_TYPES = {
    "mp3": ("mp3", "audio/mpeg"),
    "wav": ("wav", "audio/wav"),
    "opus": ("opus", "audio/ogg"),
    "pcm": ("pcm", "audio/pcm"),
    "ulaw": ("ulaw", "audio/basic"),
    "alaw": ("alaw", "audio/PCMA"),
}
_MAX_ALIGNMENT_FILE_BYTES = 1_000_000_000


def _invalid_response() -> IntegrationDownError:
    return IntegrationDownError(
        "ElevenLabs returned an invalid response", data={"reason_code": "invalid_response"}
    )


def _parse_json(response: httpx.Response) -> Any:
    if not response.is_success:
        raise IntegrationDownError(
            "ElevenLabs returned an unexpected status", data={"status": response.status_code}
        )
    try:
        return response.json()
    except ValueError:
        raise _invalid_response() from None


def _finite_number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and isfinite(value))


def _valid_character_alignment(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    characters = value.get("characters")
    start = value.get("character_start_times_seconds")
    end = value.get("character_end_times_seconds")
    return (
        isinstance(characters, list)
        and all(isinstance(char, str) for char in characters)
        and isinstance(start, list)
        and isinstance(end, list)
        and all(_finite_number(time) for time in start)
        and all(_finite_number(time) for time in end)
    )


def _valid_timed_items(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and _finite_number(item.get("start"))
        and _finite_number(item.get("end"))
        and (item.get("loss") is None or _finite_number(item["loss"]))
        for item in value
    )


def _paid_failure_data(*, charge_possible: bool, outcome_unknown: bool) -> dict[str, Any]:
    return {
        "automatic_retry_count": 0,
        "provider_executed": charge_possible,
        "charge_possible": charge_possible,
        "outcome_unknown": outcome_unknown,
        "retry_safe": False,
    }


def _identified_rows(value: Any, id_field: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _invalid_response()
    rows = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get(id_field), str) or not row[id_field]:
            raise _invalid_response()
        rows.append({key: row[key] for key in fields if key in row})
    return rows


class ElevenLabsIntegration(BaseIntegration):
    kind = vendor = "elevenlabs"
    BASE_URL = "https://api.elevenlabs.io"

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.payload.decode("utf-8"), "Accept": "application/json"}

    def _safe_id(self, *values: Any) -> str | None:
        key = self.payload.decode("utf-8")
        return next(
            (
                value
                for value in values
                if isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value)
                and (not key or key not in value)
            ),
            None,
        )

    def _response_ids(self, response: httpx.Response, data: Any = None) -> dict[str, str]:
        body = data if isinstance(data, dict) else {}
        detail = body.get("detail")
        request_id = self._safe_id(
            response.headers.get("request-id"),
            response.headers.get("x-request-id"),
            body.get("request_id"),
            detail.get("request_id") if isinstance(detail, dict) else None,
        )
        history_id = self._safe_id(
            response.headers.get("history-item-id"), body.get("history_item_id")
        )
        ids = {}
        if request_id is not None:
            ids["provider_request_id"] = request_id
        if history_id is not None:
            ids["history_item_id"] = history_id
        return ids

    def _provider_error(self, response: httpx.Response) -> dict[str, str]:
        # Error bodies may echo credentials or inputs. Expose only status-based reasons and IDs.
        reason = {401: "authentication_failed", 403: "permission_denied"}.get(
            response.status_code, "provider_failure"
        )
        ids = {}
        if response.request.method == "POST":
            try:
                data = response.json()
            except (ValueError, RecursionError):
                data = None
            ids = self._response_ids(response, data)
        return {"reason_code": reason, **ids}

    async def _post(
        self,
        operation: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        files: dict[str, tuple[str, BinaryIO, str]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            response = await self._request_with_retry(
                "POST",
                f"{self.BASE_URL}{path}",
                op=operation,
                headers=self._headers(),
                max_retries=0,
                json=json_body,
                params=params,
                data=form,
                files=files,
            )
        except IntegrationDownError as exc:
            status = exc.data.get("status")
            unknown = status is None or status >= 500
            ids = {
                key: value
                for key, value in exc.data.get("provider_error", {}).items()
                if key in {"provider_request_id", "history_item_id"}
            }
            exc.data.update(
                ids, **_paid_failure_data(charge_possible=unknown, outcome_unknown=unknown)
            )
            raise
        except OSError:
            raise IntegrationDownError(
                "ElevenLabs request could not be completed",
                data=_paid_failure_data(charge_possible=True, outcome_unknown=True),
            ) from None

        receipt = {"status": response.status_code, **self._response_ids(response)}
        try:
            data = _parse_json(response)
        except (IntegrationDownError, RecursionError):
            raise self._paid_response_error(receipt) from None
        if not isinstance(data, dict):
            raise self._paid_response_error(receipt)
        return data, {**receipt, **self._response_ids(response, data)}

    @staticmethod
    def _paid_response_error(receipt: dict[str, Any]) -> IntegrationDownError:
        return IntegrationDownError(
            "ElevenLabs returned an invalid response",
            data={
                **receipt,
                **_paid_failure_data(charge_possible=True, outcome_unknown=True),
                "reason_code": "invalid_response",
            },
        )

    async def generate_speech(
        self,
        *,
        voice_id: str,
        body: dict[str, Any],
        output_format: str | None,
        output_dir: Path | None,
        files: list[ConnectorFile],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if output_dir is None:
            raise ValidationError("output_dir is required for generated speech")
        try:
            target = Path(output_dir).stat()
        except FileNotFoundError:
            pass  # The media writer can create the directory.
        except (OSError, ValueError):
            raise ValidationError("output_dir is not accessible") from None
        else:
            if not stat.S_ISDIR(target.st_mode):
                raise ValidationError("output_dir must be a directory")
        segment = quote(voice_id, safe="")
        data, receipt = await self._post(
            "speech.generate",
            f"/v1/text-to-speech/{segment}/with-timestamps",
            params={"output_format": output_format} if output_format is not None else None,
            json_body=body,
        )
        alignments = {
            key: data[key] for key in ("alignment", "normalized_alignment") if key in data
        }
        encoded = data.get("audio_base64")
        if (
            not isinstance(encoded, str)
            or not encoded
            or not all(_valid_character_alignment(value) for value in alignments.values())
        ):
            raise self._paid_response_error(receipt)
        try:
            audio = decode_base64_media(
                encoded, vendor=self.vendor, error_message="ElevenLabs returned invalid audio"
            )
        except IntegrationDownError:
            raise self._paid_response_error(receipt) from None
        selected_format = output_format or _DEFAULT_OUTPUT_FORMAT
        ext, mime_type = _AUDIO_TYPES[selected_format.split("_", 1)[0]]
        try:
            write_media_file(
                audio,
                output_dir=output_dir,
                prefix="elevenlabs-speech",
                ext=ext,
                files=files,
                mime_type=mime_type,
            )
        except (OSError, ValueError, RuntimeError):
            raise IntegrationDownError(
                "ElevenLabs generated speech but the audio file could not be saved",
                data={
                    **receipt,
                    **_paid_failure_data(charge_possible=True, outcome_unknown=False),
                    "reason_code": "audio_persistence_failed",
                },
            ) from None
        return {"output_format": selected_format, **alignments}, receipt

    async def create_alignment(
        self, *, audio_path: Path, mime_type: str, text: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            if not audio_path.is_file():
                raise ValidationError("audio_path must refer to a regular local file")
            stream = audio_path.open("rb")
        except (OSError, ValueError):
            raise ValidationError("the supplied audio file could not be opened") from None
        with stream:
            try:
                info = os.fstat(stream.fileno())
            except OSError:
                raise ValidationError("the supplied audio file could not be inspected") from None
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size < _MAX_ALIGNMENT_FILE_BYTES:
                raise ValidationError("the supplied audio file must be nonempty and under 1 GB")
            data, receipt = await self._post(
                "alignment.create",
                "/v1/forced-alignment",
                form={"text": text},
                files={"file": (audio_path.name, stream, mime_type)},
            )
        output = {key: data[key] for key in ("words", "characters", "loss") if key in data}
        if (
            not _valid_timed_items(output.get("words"))
            or ("characters" in output and not _valid_timed_items(output["characters"]))
            or (output.get("loss") is not None and not _finite_number(output["loss"]))
        ):
            raise self._paid_response_error(receipt)
        return output, receipt

    async def _read(
        self, operation: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        call = await self.call(
            op=operation,
            method="GET",
            url=f"{self.BASE_URL}{path}",
            headers=self._headers(),
            params=params,
            response_parser=_parse_json,
            max_retries=0,
        )
        return call.data

    async def subscription(self) -> dict[str, Any]:
        data = await self._read("subscription.get", "/v1/user/subscription")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("tier"), str)
            or type(data.get("character_count")) is not int
            or type(data.get("character_limit")) is not int
        ):
            raise _invalid_response()
        return {key: data[key] for key in ("tier", "character_count", "character_limit")}

    async def test_credentials(self) -> dict[str, Any]:
        return {"ok": True, "vendor": self.vendor, **await self.subscription()}

    async def list_models(self) -> dict[str, Any]:
        data = await self._read("models.list", "/v1/models")
        return {"models": _identified_rows(data, "model_id", _MODEL_FIELDS)}

    async def list_voices(self, *, page_size: int, page_token: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token is not None:
            params["next_page_token"] = page_token
        data = await self._read("voices.list", "/v2/voices", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("has_more"), bool):
            raise _invalid_response()
        next_token = data.get("next_page_token")
        if next_token is not None and not isinstance(next_token, str):
            raise _invalid_response()
        return {
            "voices": _identified_rows(data.get("voices"), "voice_id", ("voice_id", "name")),
            "page_size": page_size,
            "page_token": page_token,
            "has_more": data["has_more"],
            "next_page_token": next_token,
        }

    async def voice_settings(self, *, voice_id: str | None = None) -> dict[str, Any]:
        if voice_id is None:
            data = await self._read("voices.settings.default.get", "/v1/voices/settings/default")
        else:
            segment = quote(voice_id, safe="")
            data = await self._read("voices.settings.get", f"/v1/voices/{segment}/settings")
        if not isinstance(data, dict):
            raise _invalid_response()
        return {key: data[key] for key in _SETTINGS_FIELDS if key in data}
