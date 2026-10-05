"""AIGNC's supplied OpenAI-compatible transport contract.

Endpoint evidence: provider-local docs/aignc.md.
Each method performs one explicit request; the calling agent owns model choice
and interpretation. Provider pricing is deliberately excluded from this wrapper.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import httpx

from stackos_connectors.connectors.aignc.contract import (
    AIGNC_AUDIO_FORMATS,
    AIGNC_AUDIO_MODELS,
    AIGNC_GROUNDING_MODELS,
    AIGNC_IMAGE_MODEL,
    AIGNC_TEXT_MODELS,
)
from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError, RateLimitedError, ValidationError
from stackos_connectors.redaction import redact_secret_values, redact_secrets
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import write_media_file


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "prompt_count"),
        ("completion_tokens", "completion_count"),
        ("total_tokens", "total_count"),
        ("google_searches", "google_searches"),
    ):
        count = value.get(source)
        if type(count) is int and count >= 0:
            result[target] = count
    for parent, source, target in (
        ("completion_tokens_details", "reasoning_tokens", "reasoning_count"),
        ("prompt_tokens_details", "cached_tokens", "cached_count"),
    ):
        details = value.get(parent)
        count = details.get(source) if isinstance(details, dict) else None
        if type(count) is int and count >= 0:
            result[target] = count
    return result


def _grounding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    queries = value.get("webSearchQueries")
    if isinstance(queries, list):
        result["webSearchQueries"] = [query for query in queries if isinstance(query, str)]
    chunks = value.get("groundingChunks")
    if isinstance(chunks, list):
        # Supports address this array by index; omitted chunk data must not
        # shift a later source into another source's citation position.
        result["groundingChunks"] = [
            {
                "web": {
                    key: item["web"][key]
                    for key in ("uri", "title")
                    if isinstance(item["web"].get(key), str)
                }
            }
            if isinstance(item, dict) and isinstance(item.get("web"), dict)
            else {}
            for item in chunks
        ]
    supports = value.get("groundingSupports")
    if isinstance(supports, list):
        clean_supports: list[dict[str, Any]] = []
        for support in supports:
            if not isinstance(support, dict):
                continue
            segment = support.get("segment")
            indices = support.get("groundingChunkIndices")
            if not isinstance(segment, dict) or not isinstance(indices, list):
                continue
            clean_segment: dict[str, Any] = {
                key: segment[key]
                for key in ("startIndex", "endIndex")
                if type(segment.get(key)) is int and segment[key] >= 0
            }
            if (
                "startIndex" in clean_segment
                and "endIndex" in clean_segment
                and clean_segment["endIndex"] < clean_segment["startIndex"]
            ):
                clean_segment = {}
            if isinstance(segment.get("text"), str):
                clean_segment["text"] = segment["text"]
            clean_indices = [
                index
                for index in indices
                if type(index) is int
                and index >= 0
                and (not isinstance(chunks, list) or index < len(chunks))
            ]
            if clean_indices and clean_segment:
                clean_supports.append(
                    {
                        "segment": clean_segment,
                        "groundingChunkIndices": clean_indices,
                    }
                )
        result["groundingSupports"] = clean_supports
    return result


class AigncIntegration(BaseIntegration):
    """Fixed-endpoint, stateless AIGNC requests using caller-supplied bearer auth."""

    kind = "aignc"
    vendor = "aignc"
    BASE_URL = "https://cli-api.f2nd.com/v1"

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        max_response_bytes: int | None = None,
        max_image_bytes: int | None = None,
        max_audio_bytes: int | None = None,
        **kwargs: Any,
    ) -> None:
        for name, value in (
            ("max_response_bytes", max_response_bytes),
            ("max_image_bytes", max_image_bytes),
            ("max_audio_bytes", max_audio_bytes),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValidationError(f"{name} must be a positive integer")
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8").strip()
        if not self._api_key or any(ord(char) < 32 or ord(char) == 127 for char in self._api_key):
            raise ValidationError(
                "AIGNC requires a nonempty bearer credential without control characters"
            )
        self._output_dir = output_dir
        self.files: list[ConnectorFile] = []
        self._max_response_bytes = max_response_bytes
        self._max_image_bytes = max_image_bytes
        self._max_audio_bytes = max_audio_bytes

    def _clean(self, value: Any) -> Any:
        return redact_secrets(redact_secret_values(value, (self._api_key,)))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    @staticmethod
    def _response_metadata(response: httpx.Response) -> dict[str, Any]:
        metadata: dict[str, Any] = {"status_code": response.status_code}
        for header, key in (
            ("cf-aig-log-id", "provider_request_id"),
            ("retry-after", "retry_after"),
        ):
            value = response.headers.get(header)
            if value:
                metadata[key] = redact_secrets(value)
        return metadata

    def _extract_response_metadata(
        self, op: str, *, request: Any, response: Any, http_response: httpx.Response
    ) -> dict[str, Any]:
        del op, request, response
        return self._clean(self._response_metadata(http_response))

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError:
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("error"), dict):
            raw = raw["error"]
        error = {
            key: raw[key]
            for key in ("message", "type", "code", "param")
            if isinstance(raw, dict) and isinstance(raw.get(key), str | int)
        }
        if not error:
            error["message"] = "AIGNC returned an undocumented error response"
        error.update(cls._response_metadata(response))
        return redact_secrets(error)

    def _failure(
        self,
        *,
        op: str,
        status: int | None,
        message: str,
        provider_error: dict[str, Any] | None = None,
    ) -> IntegrationDownError:
        return IntegrationDownError(
            message,
            data={
                "vendor": self.vendor,
                "op": op,
                "status": status,
                "provider_error": provider_error or {"message": message},
                **{
                    key: value
                    for key, value in (provider_error or {}).items()
                    if key in {"provider_request_id", "id"}
                },
                "automatic_retry_count": 0,
                "provider_executed": True,
                "outcome_unknown": op != "models.list",
                "retry_safe": op == "models.list",
                "repair_guidance": (
                    "Inspect this request's provider diagnostics before deciding to retry."
                ),
            },
        )

    async def _request_with_retry(
        self, method: str, url: str, *, op: str, **kwargs: Any
    ) -> httpx.Response:
        # Normalize the supplied OpenAI-compatible response contract.
        request = kwargs.get("json") or {}
        audio_values = tuple(
            part["input_audio"]["data"]
            for message in request.get("messages", [])
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if isinstance(part, dict)
            and isinstance(part.get("input_audio"), dict)
            and isinstance(part["input_audio"].get("data"), str)
        )
        try:
            response = await super()._request_with_retry(method, url, op=op, **kwargs)
        except (IntegrationDownError, RateLimitedError) as exc:
            data = self._clean(redact_secret_values(exc.data, audio_values))
            provider_error = data.get("provider_error")
            if isinstance(provider_error, dict):
                if provider_error.get("provider_request_id"):
                    data["provider_request_id"] = provider_error["provider_request_id"]
                data["provider_error"] = {
                    key: value[:1000] if isinstance(value, str) else value
                    for key, value in provider_error.items()
                }
            status = data.get("status")
            data.update(
                {
                    "automatic_retry_count": 0,
                    "provider_executed": True,
                    "retry_safe": method == "GET",
                    "outcome_unknown": method != "GET" and (status is None or status >= 500),
                    "repair_guidance": "Inspect the provider error and request ID before retrying.",
                }
            )
            raise type(exc)(f"AIGNC {op} request failed", data=data) from None
        metadata = self._clean(self._response_metadata(response))
        if not 200 <= response.status_code < 300:
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC returned an unexpected HTTP status",
                provider_error=metadata,
            )
        if (
            self._max_response_bytes is not None
            and len(response.content) > self._max_response_bytes
        ):
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC response exceeds the caller's byte limit",
                provider_error=metadata,
            )
        try:
            raw = response.json()
        except ValueError:
            raw = None
        if not isinstance(raw, dict) or "error" in raw:
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC returned an invalid response envelope",
                provider_error=self._clean(
                    redact_secret_values(self._provider_error(response), audio_values)
                ),
            )
        if isinstance(raw.get("id"), str):
            metadata["id"] = raw["id"]
        try:
            clean = self._normalize(raw, op=op, request=request)
        except (ValueError, OSError) as exc:
            raise self._failure(
                op=op, status=response.status_code, message=str(exc), provider_error=metadata
            ) from None
        if "provider_request_id" in metadata:
            clean["provider_request_id"] = metadata["provider_request_id"]
        return httpx.Response(
            response.status_code,
            json=self._clean(redact_secret_values(clean, audio_values)),
            request=response.request,
            headers={
                key: response.headers[key]
                for key in ("cf-aig-log-id", "retry-after")
                if key in response.headers
            },
        )

    def _normalize(
        self, raw: dict[str, Any], *, op: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        if op == "models.list":
            rows = raw.get("data")
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                for row in rows
            ):
                raise ValueError("AIGNC returned an invalid models list")
            return {"data": [{"id": row["id"]} for row in rows]}
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("AIGNC returned an invalid completion choice")
        choice = choices[0]
        message = choice.get("message")
        if (
            not isinstance(message, dict)
            or (
                op != "image.generate"
                and (not isinstance(message.get("content"), str) or message.get("images"))
            )
            or message.get("tool_calls")
            or message.get("function_call")
            or not isinstance(choice.get("finish_reason"), str)
            or not isinstance(raw.get("model"), str)
        ):
            raise ValueError("AIGNC returned an unsupported completion message")
        data: dict[str, Any] = {
            "requested_model": request["model"],
            "returned_model": raw["model"],
            "finish_reason": choice["finish_reason"],
            "usage": _usage(raw.get("usage")),
        }
        if isinstance(raw.get("id"), str):
            data["id"] = raw["id"]
        if op == "image.generate":
            encoded = self._image_base64(message)
            if not encoded:
                raise ValueError("AIGNC image is empty")
            if self._max_image_bytes is not None and len(encoded) > 4 * (
                (self._max_image_bytes + 2) // 3
            ):
                raise ValueError("AIGNC image exceeds the caller's byte limit")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("AIGNC returned invalid base64 image data") from None
            if self._max_image_bytes is not None and len(image_bytes) > self._max_image_bytes:
                raise ValueError("AIGNC image exceeds the caller's byte limit")
            if not image_bytes.startswith(b"\xff\xd8\xff") or not image_bytes.endswith(b"\xff\xd9"):
                raise ValueError("AIGNC returned invalid JPEG image data")
            try:
                image = write_media_file(
                    image_bytes,
                    output_dir=self._output_dir,
                    files=self.files,
                    prefix="aignc",
                    ext="jpg",
                )
            except OSError:
                raise ValueError("AIGNC image was generated but local persistence failed") from None
            data["data"] = [{**image, "source_model": request["model"]}]
        else:
            data["text"] = message["content"]
            if isinstance(raw.get("grounding_metadata"), dict):
                data["grounding_metadata"] = _grounding(raw["grounding_metadata"])
        return data

    @staticmethod
    def _image_base64(message: dict[str, Any]) -> str:
        # The supplied guide uses content; live replies use one inline image
        # with null content. Never select between multiple image payloads.
        if "images" not in message:
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("AIGNC returned an unsupported image message")
            return content
        images = message["images"]
        if (
            message.get("content") not in (None, "")
            or not isinstance(images, list)
            or len(images) != 1
        ):
            raise ValueError("AIGNC must return exactly one image without accompanying content")
        image = images[0]
        if (
            not isinstance(image, dict)
            or image.get("type") != "image_url"
            or not isinstance(image.get("image_url"), dict)
        ):
            raise ValueError("AIGNC returned an unsupported image entry")
        url = image["image_url"].get("url")
        prefix = "data:image/jpeg;base64,"
        if not isinstance(url, str) or not url.startswith(prefix):
            raise ValueError("AIGNC must return inline base64 JPEG image data")
        return url[len(prefix) :]

    @staticmethod
    def _text(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be nonblank text")
        return value

    @staticmethod
    def _max_tokens(value: Any) -> int:
        if type(value) is not int or value < 1:
            raise ValidationError("max_tokens must be a positive integer")
        return value

    async def models(self) -> IntegrationCallResult:
        """GET /models; only availability is learned, never capability or routing."""
        return await self.call(
            op="models.list",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers=self._headers(),
            max_retries=0,
        )

    async def chat_complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        google_search: bool = False,
    ) -> IntegrationCallResult:
        """POST /chat/completions with text messages and optional documented grounding."""
        if model not in AIGNC_TEXT_MODELS:
            raise ValidationError("model must be a reviewed AIGNC text model")
        if type(google_search) is not bool or (
            google_search and model not in AIGNC_GROUNDING_MODELS
        ):
            raise ValidationError(
                "google_search requires a reviewed Gemini text model and boolean flag"
            )
        if not isinstance(messages, list) or not messages:
            raise ValidationError("messages must contain text messages")
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message.get("role") not in {"system", "user", "assistant"}
            ):
                raise ValidationError(
                    "messages require only role and text content; tool messages are unsupported"
                )
            self._text(message["content"], field="message content")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_tokens(max_tokens),
            "stream": False,
        }
        if google_search:
            body["tools"] = [{"google_search": {}}]
        return await self.call(
            op="chat.complete",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            max_retries=0,
        )

    async def generate_image(self, *, prompt: str, max_tokens: int) -> IntegrationCallResult:
        """POST /chat/completions with the documented image model; persist its JPEG."""
        if self._output_dir is None:
            raise ValidationError("AIGNC image generation requires output_dir")
        body = {
            "model": AIGNC_IMAGE_MODEL,
            "messages": [{"role": "user", "content": self._text(prompt, field="prompt")}],
            "max_tokens": self._max_tokens(max_tokens),
            "stream": False,
        }
        return await self.call(
            op="image.generate",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            max_retries=0,
        )

    async def analyze_audio(
        self, *, model: str, instruction: str, audio_path: Path, format: str, max_tokens: int
    ) -> IntegrationCallResult:
        """POST /chat/completions with the caller's local audio file."""
        if model not in AIGNC_AUDIO_MODELS:
            raise ValidationError("model must be an explicitly documented AIGNC audio model")
        instruction = self._text(instruction, field="instruction")
        max_tokens = self._max_tokens(max_tokens)
        if format not in AIGNC_AUDIO_FORMATS:
            raise ValidationError("audio format must be wav, mp3, aac, flac, or ogg")
        try:
            if not audio_path.is_file() or audio_path.stat().st_size == 0:
                raise ValidationError("audio must be a nonempty regular file")
            if (
                self._max_audio_bytes is not None
                and audio_path.stat().st_size > self._max_audio_bytes
            ):
                raise ValidationError("audio exceeds the caller's byte limit")
            with audio_path.open("rb") as stream:
                raw = stream.read(
                    self._max_audio_bytes + 1 if self._max_audio_bytes is not None else -1
                )
        except OSError:
            raise ValidationError("audio file cannot be read") from None
        if self._max_audio_bytes is not None and len(raw) > self._max_audio_bytes:
            raise ValidationError("audio exceeds the caller's byte limit")
        valid = {
            "wav": raw.startswith(b"RIFF") and raw[8:12] == b"WAVE",
            "mp3": raw.startswith(b"ID3")
            or (len(raw) > 1 and raw[0] == 255 and raw[1] & 0xE6 == 0xE2),
            "aac": len(raw) > 1 and raw[0] == 255 and raw[1] & 0xF6 == 0xF0,
            "flac": raw.startswith(b"fLaC"),
            "ogg": raw.startswith(b"OggS"),
        }[format]
        if not valid:
            raise ValidationError("audio bytes do not match the declared format")
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(raw).decode("ascii"),
                                "format": format,
                            },
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        return await self.call(
            op="audio.analyze",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            max_retries=0,
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.models()
        return {
            "ok": True,
            "vendor": self.vendor,
            "model_count": len(result.data["data"]),
            "generation_verified": False,
        }


__all__ = ["AigncIntegration"]
