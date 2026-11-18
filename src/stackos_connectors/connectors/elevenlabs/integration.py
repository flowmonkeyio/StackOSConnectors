"""ElevenLabs REST reads and subscription-backed credential verification."""

from typing import Any
from urllib.parse import quote

import httpx

from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration

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


def _identified_rows(
    value: Any, id_field: str, fields: tuple[str, ...]
) -> list[dict[str, Any]]:
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

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> dict[str, str]:
        # Provider error bodies can echo credentials. Status alone supplies the diagnostic.
        reason = {401: "authentication_failed", 403: "permission_denied"}.get(
            response.status_code, "provider_failure"
        )
        return {"reason_code": reason}

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
