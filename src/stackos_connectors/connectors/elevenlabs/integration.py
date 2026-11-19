"""ElevenLabs REST protocol and subscription-backed credential verification."""

import os
import re
import stat
from math import isfinite
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote
from uuid import uuid4

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError, ValidationError
from stackos_connectors.shared.base import BaseIntegration
from stackos_connectors.shared.media import (
    check_media_output_directory,
    decode_base64_media,
    write_media_file,
)

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
_SHARED_VOICE_FIELDS = (
    "public_owner_id",
    "voice_id",
    "name",
    "preview_url",
    "notice_period",
    "rate",
    "fiat_rate",
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
_PREVIEW_EXTENSIONS = {
    **{mime.lower(): ext for ext, mime in _AUDIO_TYPES.values()},
    "audio/mp3": "mp3",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
    "audio/vnd.wave": "wav",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/x-pcm": "pcm",
    "audio/l16": "pcm",
    "audio/pcmu": "ulaw",
    "application/ogg": "ogg",
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


def _post_failure_data(*, outcome_unknown: bool, charge_possible: bool | None) -> dict[str, Any]:
    return {
        "automatic_retry_count": 0,
        "provider_executed": True,
        "outcome_unknown": outcome_unknown,
        "retry_safe": False,
        **({"charge_possible": charge_possible} if charge_possible is not None else {}),
    }


def _prepare_audio_output(output_dir: Path | None, purpose: str) -> Path:
    if output_dir is None:
        raise ValidationError(f"output_dir is required for {purpose}")
    try:
        check_media_output_directory(output_dir)
    except (OSError, ValueError, RuntimeError):
        raise ValidationError("output_dir must allow creating an audio file") from None
    return output_dir


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
        reports_charge: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        receipt: dict[str, Any] = {}

        def capture_headers(response: httpx.Response) -> None:
            receipt.update(status=response.status_code, **self._response_ids(response))
            if response.status_code == 429:
                receipt["retry_after"] = self._parse_retry_after(response)

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
                on_response_headers=capture_headers,
            )
        except (IntegrationDownError, OSError) as exc:
            error = (
                exc
                if isinstance(exc, IntegrationDownError)
                else IntegrationDownError("ElevenLabs request could not be completed")
            )
            status = receipt.get("status", error.data.get("status"))
            unknown = status is None or not 400 <= status < 500
            ids = {
                key: value
                for key, value in error.data.get("provider_error", {}).items()
                if key in {"provider_request_id", "history_item_id"}
            }
            error.data.update(
                receipt,
                **ids,
                **_post_failure_data(
                    outcome_unknown=unknown, charge_possible=unknown if reports_charge else None
                ),
            )
            raise error from None

        try:
            data = _parse_json(response)
        except (IntegrationDownError, RecursionError):
            raise self._post_response_error(receipt, reports_charge=reports_charge) from None
        if not isinstance(data, dict):
            raise self._post_response_error(receipt, reports_charge=reports_charge)
        return data, {**receipt, **self._response_ids(response, data)}

    @staticmethod
    def _post_response_error(
        receipt: dict[str, Any], *, reports_charge: bool = True
    ) -> IntegrationDownError:
        return IntegrationDownError(
            "ElevenLabs returned an invalid response",
            data={
                **receipt,
                **_post_failure_data(
                    outcome_unknown=True, charge_possible=True if reports_charge else None
                ),
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
        output_dir = _prepare_audio_output(output_dir, "generated speech")
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
            raise self._post_response_error(receipt)
        try:
            audio = decode_base64_media(
                encoded, vendor=self.vendor, error_message="ElevenLabs returned invalid audio"
            )
        except IntegrationDownError:
            raise self._post_response_error(receipt) from None
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
                    **_post_failure_data(charge_possible=True, outcome_unknown=False),
                    "reason_code": "audio_persistence_failed",
                },
            ) from None
        return {"output_format": selected_format, **alignments}, receipt

    async def design_voice(
        self,
        *,
        body: dict[str, Any],
        output_format: str | None,
        output_dir: Path | None,
        files: list[ConnectorFile],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        output_dir = _prepare_audio_output(output_dir, "voice previews")
        data, receipt = await self._post(
            "voices.design",
            "/v1/text-to-voice/design",
            params={"output_format": output_format} if output_format is not None else None,
            json_body=body,
        )
        previews = data.get("previews")
        if not isinstance(previews, list) or not previews:
            raise self._post_response_error(receipt)

        # Each preview gets its own path even if the provider returns identical audio.
        batch_id = uuid4().hex
        first_file_index = len(files)
        rows = [
            self._save_voice_preview(
                preview,
                output_dir=output_dir,
                prefix=f"elevenlabs-voice-preview-{batch_id}-{index}",
                files=files,
            )
            for index, preview in enumerate(previews)
        ]
        saved = len(files) - first_file_index
        status = "success"
        if saved != len(rows):
            status = "partial" if saved else "failed"
            receipt.update(_post_failure_data(outcome_unknown=False, charge_possible=True))

        output = {"status": status, "previews": rows}
        if isinstance(data.get("text"), str):
            output["text"] = data["text"]
        return output, receipt

    def _save_voice_preview(
        self, preview: Any, *, output_dir: Path, prefix: str, files: list[ConnectorFile]
    ) -> dict[str, Any]:
        row: dict[str, Any] = {}
        if not isinstance(preview, dict):
            return {"reason_code": "invalid_preview"}

        voice_id = preview.get("generated_voice_id")
        if isinstance(voice_id, str) and voice_id.strip():
            row["generated_voice_id"] = voice_id
        duration = preview.get("duration_secs")
        if _finite_number(duration) and duration >= 0:
            row["duration_secs"] = duration
        media_type = preview.get("media_type")
        ext = None
        if isinstance(media_type, str) and media_type.isascii() and media_type.isprintable():
            base_type = media_type.partition(";")[0].strip().lower()
            ext = _PREVIEW_EXTENSIONS.get(base_type)
            if ext is None and re.fullmatch(r"audio/[a-z0-9][a-z0-9.+_-]*", base_type):
                ext = "bin"
            if ext is not None:
                row["media_type"] = media_type
        language = preview.get("language")
        if "language" in preview and (language is None or isinstance(language, str)):
            row["language"] = language

        encoded = preview.get("audio_base_64")
        if (
            "generated_voice_id" not in row
            or "duration_secs" not in row
            or ext is None
            or not isinstance(encoded, str)
            or not encoded
        ):
            return {**row, "reason_code": "invalid_preview"}
        try:
            audio = decode_base64_media(
                encoded,
                vendor=self.vendor,
                error_message="ElevenLabs returned invalid preview audio",
            )
        except IntegrationDownError:
            return {**row, "reason_code": "invalid_preview"}

        file_index = len(files)
        try:
            write_media_file(
                audio,
                output_dir=output_dir,
                prefix=prefix,
                ext=ext,
                files=files,
                mime_type=row["media_type"],
            )
        except (OSError, ValueError, RuntimeError):
            return {**row, "reason_code": "audio_persistence_failed"}
        return {**row, "file_index": file_index}

    async def create_voice(self, *, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        data, receipt = await self._post(
            "voices.create", "/v1/text-to-voice", json_body=body, reports_charge=False
        )
        voice_id = data.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise self._post_response_error(receipt, reports_charge=False)
        return {"voice_id": voice_id}, receipt

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
            raise self._post_response_error(receipt)
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

    async def search_shared_voices(
        self, *, search: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search is not None:
            params["search"] = search
        data = await self._read("library.search", "/v1/shared-voices", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("has_more"), bool):
            raise _invalid_response()

        voices = _identified_rows(data.get("voices"), "voice_id", _SHARED_VOICE_FIELDS)
        for voice in voices:
            owner = voice.get("public_owner_id")
            preview = voice.get("preview_url")
            notice = voice.get("notice_period")
            if (
                not isinstance(owner, str)
                or not owner.strip()
                or not voice["voice_id"].strip()
                or not isinstance(voice.get("name"), str)
                or (preview is not None and not isinstance(preview, str))
                or (notice is not None and type(notice) is not int)
                or any(
                    voice.get(key) is not None and not _finite_number(voice[key])
                    for key in ("rate", "fiat_rate")
                )
            ):
                raise _invalid_response()
        return {"has_more": data["has_more"], "voices": voices}

    async def add_shared_voice(
        self, *, public_owner_id: str, voice_id: str, new_name: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        owner_segment = quote(public_owner_id, safe="")
        voice_segment = quote(voice_id, safe="")
        data, receipt = await self._post(
            "library.add",
            f"/v1/voices/add/{owner_segment}/{voice_segment}",
            json_body={"new_name": new_name},
            reports_charge=False,
        )
        added_voice_id = data.get("voice_id")
        if not isinstance(added_voice_id, str) or not added_voice_id.strip():
            raise self._post_response_error(receipt, reports_charge=False)
        return {"voice_id": added_voice_id}, receipt
