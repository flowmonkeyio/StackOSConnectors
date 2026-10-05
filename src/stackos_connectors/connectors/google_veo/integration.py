"""Native google veo requests and caller-owned media files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import (
    download_generated_media,
    image_inline_data,
    write_media_file,
)


class GoogleVeoIntegration(BaseIntegration):
    """Wrapper for Gemini API Veo video generation."""

    kind = "google-veo"
    vendor = "google-veo"

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    API_KEY_HOSTS: ClassVar[frozenset[str]] = frozenset({"generativelanguage.googleapis.com"})
    DEFAULT_MODEL = "veo-3.1-generate-preview"
    MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "veo-3.1-generate-preview",
            "veo-3.1-fast-generate-preview",
            "veo-3.1-lite-generate-preview",
        }
    )
    ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset({"16:9", "9:16"})
    RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"720p", "1080p", "4k"})
    RESOLUTIONS_BY_MODEL: ClassVar[dict[str, frozenset[str]]] = {
        "veo-3.1-generate-preview": frozenset({"720p", "1080p", "4k"}),
        "veo-3.1-fast-generate-preview": frozenset({"720p", "1080p", "4k"}),
        "veo-3.1-lite-generate-preview": frozenset({"720p", "1080p"}),
    }
    PERSON_GENERATION_VALUES: ClassVar[frozenset[str]] = frozenset({"allow_all", "allow_adult"})
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png"})
    MAX_INPUT_IMAGE_BYTES = 20_000_000
    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"done"})

    def __init__(
        self,
        *,
        payload: bytes,
        http: httpx.AsyncClient,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(payload=payload, http=http, **kwargs)
        self._output_dir = output_dir
        self.files: list[ConnectorFile] = []
        self.provider_executed = False
        self.submission_http_succeeded = False
        self.generation_outcome: str | None = None
        self.receipt: dict[str, Any] = {}

    def _auth_headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.payload.decode("utf-8"),
            "Content-Type": "application/json",
        }

    async def generate_video(
        self,
        *,
        prompt: str,
        model: str,
        mode: str,
        duration_seconds: int | None = None,
        aspect_ratio: str,
        resolution: str | None = None,
        input_image_path: Path | None = None,
        last_frame_path: Path | None = None,
        enhance_prompt: bool | None = None,
        person_generation: str | None = None,
        seed: int | None = None,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        instance: dict[str, Any] = {"prompt": prompt}
        if mode in {"image-to-video", "first-last-frame"}:
            if input_image_path is None:
                raise IntegrationDownError(
                    "Google Veo image modes require an input image",
                    data={"vendor": self.vendor, "model": model, "mode": mode},
                )
            instance["image"] = image_inline_data(
                input_image_path,
                allowed_suffixes=self.INPUT_IMAGE_FORMATS,
                max_bytes=self.MAX_INPUT_IMAGE_BYTES,
                vendor=self.vendor,
            )
        if mode == "first-last-frame":
            if last_frame_path is None:
                raise IntegrationDownError(
                    "Google Veo first-last-frame mode requires a last frame image",
                    data={"vendor": self.vendor, "model": model},
                )
            instance["lastFrame"] = image_inline_data(
                last_frame_path,
                allowed_suffixes=self.INPUT_IMAGE_FORMATS,
                max_bytes=self.MAX_INPUT_IMAGE_BYTES,
                vendor=self.vendor,
            )
        parameters: dict[str, Any] = {"aspectRatio": aspect_ratio}
        if duration_seconds is not None:
            parameters["durationSeconds"] = duration_seconds
        if resolution is not None:
            parameters["resolution"] = resolution
        if enhance_prompt is not None:
            parameters["enhancePrompt"] = enhance_prompt
        if person_generation is not None:
            parameters["personGeneration"] = person_generation
        if seed is not None:
            parameters["seed"] = seed
        body = {
            "instances": [instance],
            "parameters": parameters,
        }
        submitted = await self.call(
            max_retries=0,
            op="video.generate",
            method="POST",
            url=f"{self.BASE_URL}/models/{model}:predictLongRunning",
            json_body=body,
            headers=self._auth_headers(),
        )
        operation_name = self._operation_name(submitted.data)
        poll_result = await self._poll_operation(
            operation_name=operation_name,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        persisted = await self._persist_video_response(
            poll_result.data,
            operation_name=operation_name,
            model=model,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=submitted.cost_usd + poll_result.cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    async def _poll_operation(
        self,
        *,
        operation_name: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            operation_url = self._operation_url(operation_name)
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=operation_url,
                headers=self._api_key_headers_for_url(operation_url),
            )
            if not isinstance(poll_result.data, dict):
                raise IntegrationDownError(
                    "Google Veo poll returned a non-JSON response",
                    data={"vendor": self.vendor, "operation_name": operation_name},
                )
            if isinstance(poll_result.data.get("error"), dict):
                self.generation_outcome = "failed"
                raise IntegrationDownError(
                    "Google Veo operation failed",
                    data={
                        "vendor": self.vendor,
                        "operation_name": operation_name,
                        "error": poll_result.data["error"],
                    },
                )
            if poll_result.data.get("done") is True:
                self.generation_outcome = "succeeded"
                return poll_result
            await asyncio.sleep(poll_interval_seconds)
        raise IntegrationDownError(
            "Google Veo video generation timed out",
            data={"vendor": self.vendor, "operation_name": operation_name},
        )

    async def _persist_video_response(
        self,
        data: dict[str, Any],
        *,
        operation_name: str,
        model: str,
    ) -> dict[str, Any]:
        samples = _generated_samples(data)
        if not samples:
            raise IntegrationDownError(
                "Google Veo operation completed without generated videos",
                data={"vendor": self.vendor, "operation_name": operation_name},
            )
        if self._output_dir is None:
            return data
        persisted: list[dict[str, Any]] = []
        for index, sample in enumerate(samples):
            video = sample.get("video") if isinstance(sample, dict) else None
            if not isinstance(video, dict) or not isinstance(video.get("uri"), str):
                raise IntegrationDownError(
                    "Google Veo generated sample did not include a video URI",
                    data={"vendor": self.vendor, "operation_name": operation_name, "index": index},
                )
            video_uri = str(video["uri"])
            raw, ext = await download_generated_media(
                self,
                video_uri,
                fallback_ext="mp4",
                headers=self._api_key_headers_for_url(video_uri),
                empty_message="Google Veo returned an empty video download",
            )
            item = {
                **{key: value for key, value in video.items() if key != "uri"},
                **write_media_file(
                    raw,
                    output_dir=self._output_dir,
                    files=self.files,
                    prefix="google-veo-video",
                    ext=ext,
                ),
                "source_model": model,
                "operation_name": operation_name,
                "sample_index": index,
            }
            persisted.append(item)
        return {
            "operation_name": operation_name,
            "status": "done",
            "model": model,
            "data": persisted,
        }

    @staticmethod
    def _operation_name(data: Any) -> str:
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            return data["name"]
        raise IntegrationDownError(
            "Google Veo generation did not return an operation name",
            data={"vendor": GoogleVeoIntegration.vendor},
        )

    @classmethod
    def _operation_url(cls, operation_name: str) -> str:
        if operation_name.startswith("http://") or operation_name.startswith("https://"):
            if not cls._is_google_api_url(operation_name):
                raise IntegrationDownError(
                    "Google Veo operation URL was not a Google API URL",
                    data={"vendor": cls.vendor},
                )
            return operation_name
        return f"{cls.BASE_URL}/{operation_name.lstrip('/')}"

    @classmethod
    def _is_google_api_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in cls.API_KEY_HOSTS

    def _api_key_headers_for_url(self, url: str) -> dict[str, str] | None:
        if self._is_google_api_url(url):
            return {"x-goog-api-key": self.payload.decode("utf-8")}
        return None

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "probe_mode": "non_billable_format_only",
            "provider_executed": False,
            "summary": "No provider request was sent; live credential validity is unverified.",
        }

    def _provider_error(self, response):
        for name in ("x-request-id", "request-id", "x-goog-request-id"):
            if response.headers.get(name):
                self.receipt.setdefault("provider_request_id", response.headers[name])
                ids = self.receipt.setdefault("provider_request_ids", [])
                if response.headers[name] not in ids:
                    ids.append(response.headers[name])
        return super()._provider_error(response)

    async def _request_with_retry(self, method: str, url: str, *, op: str, **kwargs: Any):
        self.provider_executed = True
        response = await super()._request_with_retry(method, url, op=op, **kwargs)
        if method == "POST" and 200 <= response.status_code < 300:
            self.submission_http_succeeded = True
        for name in ("x-request-id", "request-id", "x-goog-request-id"):
            if response.headers.get(name):
                self.receipt.setdefault("provider_request_id", response.headers[name])
                ids = self.receipt.setdefault("provider_request_ids", [])
                if response.headers[name] not in ids:
                    ids.append(response.headers[name])
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            for key in ("name",):
                value = body
                for segment in key.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                if isinstance(value, str) and value:
                    self.receipt.setdefault(key.rsplit(".", 1)[-1], value)
        return response


def _generated_samples(data: dict[str, Any]) -> list[Any]:
    response = data.get("response")
    if not isinstance(response, dict):
        return []
    generate_video_response = response.get("generateVideoResponse")
    if isinstance(generate_video_response, dict):
        samples = generate_video_response.get("generatedSamples")
        if isinstance(samples, list):
            return samples
    generated_videos = response.get("generatedVideos")
    if isinstance(generated_videos, list):
        return generated_videos
    return []


__all__ = ["GoogleVeoIntegration"]
