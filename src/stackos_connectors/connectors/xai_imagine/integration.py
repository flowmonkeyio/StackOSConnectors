"""Native xai imagine requests and caller-owned media files."""

from __future__ import annotations

import asyncio
import base64
import binascii
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import download_generated_media, write_media_file


class XAIImagineIntegration(BaseIntegration):
    """Wrapper for xAI Imagine image and video endpoints."""

    kind = "xai-imagine"
    vendor = "xai-imagine"

    BASE_URL = "https://api.x.ai/v1"
    IMAGE_MODEL = "grok-imagine-image-quality"
    VIDEO_MODEL = "grok-imagine-video"
    IMAGE_INPUT_MAX_BYTES = 20 * 1024 * 1024

    IMAGE_ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {
            "1:1",
            "16:9",
            "9:16",
            "4:3",
            "3:4",
            "3:2",
            "2:3",
            "2:1",
            "1:2",
            "19.5:9",
            "9:19.5",
            "20:9",
            "9:20",
            "auto",
        }
    )
    IMAGE_RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"1k", "2k"})
    VIDEO_ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
    )
    VIDEO_RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"480p", "720p"})
    VIDEO_MODELS: ClassVar[frozenset[str]] = frozenset({VIDEO_MODEL})
    TERMINAL_VIDEO_STATUSES: ClassVar[frozenset[str]] = frozenset({"done", "failed", "expired"})
    # Official pricing refs:
    # https://docs.x.ai/developers/pricing
    #
    # Provider-reported usage denominates currency in USD ticks.
    _USD_TICKS_PER_DOLLAR = 10_000_000_000

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
            "Authorization": f"Bearer {self.payload.decode('utf-8')}",
            "Content-Type": "application/json",
        }

    @classmethod
    def _cost_from_usage_usd(cls, response: Any) -> float | None:
        if not isinstance(response, dict):
            return None
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        raw_ticks = usage.get("cost_in_usd_ticks")
        if not isinstance(raw_ticks, int | float | str) or isinstance(raw_ticks, bool):
            return None
        try:
            ticks = int(raw_ticks)
        except (TypeError, ValueError):
            return None
        if ticks < 0:
            return None
        return ticks / cls._USD_TICKS_PER_DOLLAR

    async def generate_image(
        self, *, prompt: str, aspect_ratio: str, resolution: str, n: int, model: str
    ) -> IntegrationCallResult:
        body = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "n": n,
            "response_format": "b64_json",
        }
        result = await self.call(
            max_retries=0,
            op="image.generate",
            method="POST",
            url=f"{self.BASE_URL}/images/generations",
            json_body=body,
            headers=self._auth_headers(),
        )
        data = await self._persist_image_response(result.data, model=model)
        return IntegrationCallResult(
            data=data,
            cost_usd=self._cost_from_usage_usd(result.data) or 0.0,
            duration_ms=result.duration_ms,
        )

    async def edit_image(
        self,
        *,
        prompt: str,
        input_image_paths: list[Path],
        aspect_ratio: str | None = None,
        resolution: str,
        model: str,
    ) -> IntegrationCallResult:
        if not input_image_paths:
            raise IntegrationDownError(
                "xAI image edit requires at least one input image",
                data={"vendor": self.vendor},
            )
        images = [self._image_payload(path) for path in input_image_paths]
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "resolution": resolution,
        }
        if aspect_ratio is not None and len(images) > 1:
            body["aspect_ratio"] = aspect_ratio
        if len(images) == 1:
            body["image"] = images[0]
        else:
            body["images"] = images
        result = await self.call(
            max_retries=0,
            op="image.edit",
            method="POST",
            url=f"{self.BASE_URL}/images/edits",
            json_body=body,
            headers=self._auth_headers(),
        )
        data = await self._persist_image_response(result.data, model=model)
        return IntegrationCallResult(
            data=data,
            cost_usd=self._cost_from_usage_usd(result.data) or 0.0,
            duration_ms=result.duration_ms,
        )

    async def generate_video(
        self,
        *,
        prompt: str,
        duration: int,
        aspect_ratio: str,
        resolution: str,
        model: str,
        image_path: Path | None = None,
        reference_image_paths: list[Path] | None = None,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        if image_path is not None and reference_image_paths:
            raise IntegrationDownError(
                "xAI video generation accepts image-to-video or reference-to-video, not both",
                data={"vendor": self.vendor, "model": model},
            )
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if image_path is not None:
            body["image"] = self._image_payload(image_path)
        if reference_image_paths:
            body["reference_images"] = [self._image_payload(path) for path in reference_image_paths]
        submitted = await self.call(
            max_retries=0,
            op="video.generate",
            method="POST",
            url=f"{self.BASE_URL}/videos/generations",
            json_body=body,
            headers=self._auth_headers(),
        )
        if not isinstance(submitted.data, dict) or not isinstance(
            submitted.data.get("request_id"), str
        ):
            raise IntegrationDownError(
                "xAI video generation did not return a request_id",
                data={"vendor": self.vendor, "model": model},
            )
        request_id = submitted.data["request_id"]
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=f"{self.BASE_URL}/videos/{request_id}",
                headers={"Authorization": self._auth_headers()["Authorization"]},
            )
            if not isinstance(poll_result.data, dict):
                raise IntegrationDownError(
                    "xAI video poll returned a non-JSON response",
                    data={"vendor": self.vendor, "request_id": request_id},
                )
            status = str(poll_result.data.get("status") or "")
            if status in self.TERMINAL_VIDEO_STATUSES:
                self.generation_outcome = "succeeded" if status == "done" else "failed"
                break
            await asyncio.sleep(poll_interval_seconds)
        else:
            raise IntegrationDownError(
                "xAI video generation timed out",
                data={"vendor": self.vendor, "request_id": request_id},
            )
        assert poll_result is not None
        data = poll_result.data
        status = str(data.get("status") or "")
        if status != "done":
            raise IntegrationDownError(
                f"xAI video generation ended with status {status or 'unknown'}",
                data={
                    "vendor": self.vendor,
                    "request_id": request_id,
                    "status": status,
                    "error": data.get("error"),
                },
            )
        persisted = await self._persist_video_response(data, request_id=request_id, model=model)
        cost_usd = self._cost_from_usage_usd(data) or 0.0
        return IntegrationCallResult(
            data=persisted,
            cost_usd=cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    async def _persist_image_response(self, data: Any, *, model: str) -> Any:
        if self._output_dir is None or not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data
        out = dict(data)
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            raw: bytes | None = None
            ext = "jpg"
            if isinstance(item.get("b64_json"), str):
                try:
                    raw = base64.b64decode(item["b64_json"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise IntegrationDownError(
                        "xAI Imagine returned invalid base64 image data",
                        data={"vendor": self.vendor, "model": model},
                    ) from exc
            elif isinstance(item.get("url"), str):
                if item["url"]:
                    self.generation_outcome = "succeeded"
                raw, ext = await self._download_media(str(item["url"]), fallback_ext="jpg")
            if raw is None:
                persisted.append(item)
                continue
            if raw:
                self.generation_outcome = "succeeded"
            clean = {k: v for k, v in item.items() if k not in {"b64_json", "url"}}
            clean.update(self._write_media(raw, prefix="xai-image", ext=ext))
            clean["source_model"] = str(item.get("model") or model)
            persisted.append(clean)
        out["data"] = persisted
        return out

    async def _persist_video_response(
        self,
        data: dict[str, Any],
        *,
        request_id: str,
        model: str,
    ) -> dict[str, Any]:
        video = data.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("url"), str):
            raise IntegrationDownError(
                "xAI video generation completed without a video URL",
                data={"vendor": self.vendor, "request_id": request_id},
            )
        raw, ext = await self._download_media(str(video["url"]), fallback_ext="mp4")
        item = {
            **{k: v for k, v in video.items() if k != "url"},
            **self._write_media(raw, prefix="xai-video", ext=ext),
            "source_model": str(data.get("model") or model),
            "request_id": request_id,
        }
        out = {
            "request_id": request_id,
            "status": "done",
            "model": str(data.get("model") or model),
            "data": [item],
        }
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        return out

    async def _download_media(self, url: str, *, fallback_ext: str) -> tuple[bytes, str]:
        return await download_generated_media(
            self,
            url,
            fallback_ext=fallback_ext,
            empty_message="xAI Imagine returned an empty media download",
        )

    def _write_media(self, raw: bytes, *, prefix: str, ext: str) -> dict[str, str]:
        return write_media_file(
            raw, output_dir=self._output_dir, files=self.files, prefix=prefix, ext=ext
        )

    def _image_payload(self, path: Path) -> dict[str, str]:
        suffix = path.suffix.lower().lstrip(".")
        mime = _image_mime_type(suffix)
        if mime is None:
            raise IntegrationDownError(
                "xAI Imagine input images must be PNG or JPEG",
                data={"vendor": self.vendor, "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "xAI Imagine input image could not be read",
                data={"vendor": self.vendor, "file": path.name},
            ) from exc
        if len(raw) > self.IMAGE_INPUT_MAX_BYTES:
            raise IntegrationDownError(
                "xAI Imagine input images must be at most 20 MiB",
                data={
                    "vendor": self.vendor,
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": self.IMAGE_INPUT_MAX_BYTES,
                },
            )
        encoded = base64.b64encode(raw).decode("ascii")
        return {"url": f"data:{mime};base64,{encoded}"}

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers={"Authorization": self._auth_headers()["Authorization"]},
        )
        models_count = len(result.data.get("data", [])) if isinstance(result.data, dict) else 0
        return {"ok": True, "vendor": self.vendor, "models_count": models_count}

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
            for key in ("request_id",):
                value = body
                for segment in key.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                if isinstance(value, str) and value:
                    self.receipt.setdefault(key.rsplit(".", 1)[-1], value)
        return response


def _image_mime_type(suffix: str) -> str | None:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    return None


__all__ = ["XAIImagineIntegration"]
