"""Native ideogram images requests and caller-owned media files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import download_generated_media, write_media_file


class IdeogramImagesIntegration(BaseIntegration):
    """Wrapper for Ideogram first-party image endpoints."""

    kind = "ideogram"
    vendor = "ideogram"

    BASE_URL = "https://api.ideogram.ai/v1"
    MODEL = "ideogram-v4"
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png", "webp"})
    MAX_INPUT_IMAGE_BYTES = 10_000_000
    RENDERING_SPEEDS: ClassVar[frozenset[str]] = frozenset({"TURBO", "DEFAULT", "QUALITY"})
    RESOLUTIONS: ClassVar[tuple[str, ...]] = (
        "2048x2048",
        "1440x2880",
        "2880x1440",
        "1664x2496",
        "2496x1664",
        "1792x2240",
        "2240x1792",
        "1440x2560",
        "2560x1440",
        "1600x2560",
        "2560x1600",
        "1728x2304",
        "2304x1728",
        "1296x3168",
        "3168x1296",
        "1152x2944",
        "2944x1152",
        "1248x3328",
        "3328x1248",
        "1280x3072",
        "3072x1280",
        "1024x3072",
        "3072x1024",
    )

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
            "Api-Key": self.payload.decode("utf-8"),
            "Accept": "application/json",
        }

    async def generate_image(
        self,
        *,
        text_prompt: str,
        resolution: str | None = None,
        rendering_speed: str,
        enable_copyright_detection: bool | None = None,
    ) -> IntegrationCallResult:
        data_body = _compact_form(
            {
                "text_prompt": text_prompt,
                "resolution": resolution,
                "rendering_speed": rendering_speed,
                "enable_copyright_detection": enable_copyright_detection,
            }
        )
        result = await self.call(
            max_retries=0,
            op="image.generate",
            method="POST",
            url=f"{self.BASE_URL}/ideogram-v4/generate",
            files=_multipart_fields(data_body),
            headers=self._auth_headers(),
        )
        data = await self._persist_url_response(result.data)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def remix_image(
        self,
        *,
        text_prompt: str,
        image_path: Path,
        image_weight: int | None = None,
        resolution: str | None = None,
        rendering_speed: str,
        enable_copyright_detection: bool | None = None,
    ) -> IntegrationCallResult:
        raw, mime_type = self.ensure_image_preflight(image_path)
        data_body = _compact_form(
            {
                "text_prompt": text_prompt,
                "image_weight": image_weight,
                "resolution": resolution,
                "rendering_speed": rendering_speed,
                "enable_copyright_detection": enable_copyright_detection,
            }
        )
        result = await self.call(
            max_retries=0,
            op="image.remix",
            method="POST",
            url=f"{self.BASE_URL}/ideogram-v4/remix",
            data_body=data_body,
            files={"image": (image_path.name, raw, mime_type)},
            headers=self._auth_headers(),
        )
        data = await self._persist_url_response(result.data)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    @classmethod
    def ensure_image_preflight(cls, path: Path) -> tuple[bytes, str]:
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in cls.INPUT_IMAGE_FORMATS:
            raise IntegrationDownError(
                "Ideogram remix image inputs must be JPEG, PNG, or WEBP",
                data={"vendor": cls.vendor, "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "Ideogram remix input image could not be read",
                data={"vendor": cls.vendor, "file": path.name},
            ) from exc
        if len(raw) > cls.MAX_INPUT_IMAGE_BYTES:
            raise IntegrationDownError(
                "Ideogram remix image inputs must be at most 10 MB",
                data={
                    "vendor": cls.vendor,
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": cls.MAX_INPUT_IMAGE_BYTES,
                },
            )
        if not _matches_image_signature(raw, suffix):
            raise IntegrationDownError(
                "Ideogram remix image inputs must be valid JPEG, PNG, or WEBP bytes",
                data={"vendor": cls.vendor, "file": path.name},
            )
        return raw, _mime_type_for_suffix(suffix)

    async def _persist_url_response(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data
        if self._output_dir is None and _contains_provider_url(items):
            raise IntegrationDownError(
                "Ideogram generated image URLs require output_dir",
                data={"vendor": self.vendor},
            )
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            provider_url = item.get("url")
            if not isinstance(provider_url, str) or not provider_url:
                persisted.append(item)
                continue
            self.generation_outcome = "succeeded"
            persisted.append(await self._persist_provider_url(item, provider_url))
        clean = {key: value for key, value in data.items() if key != "data"}
        clean["data"] = persisted
        return clean

    async def _persist_provider_url(
        self,
        item: dict[str, Any],
        provider_url: str,
    ) -> dict[str, Any]:
        assert self._output_dir is not None
        raw, ext = await download_generated_media(
            self,
            provider_url,
            fallback_ext="png",
            empty_message="Ideogram returned an empty image download",
        )
        file_info = write_media_file(
            raw,
            output_dir=self._output_dir,
            files=self.files,
            prefix="ideogram",
            ext=ext,
        )
        clean = {key: value for key, value in item.items() if key != "url"}
        clean.update(file_info)
        clean["source_model"] = self.MODEL
        clean["provider_url_persisted"] = True
        return clean

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
            for key in ("request_id",):
                value = body
                for segment in key.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                if isinstance(value, str) and value:
                    self.receipt.setdefault(key.rsplit(".", 1)[-1], value)
        return response


def _compact_form(values: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def _multipart_fields(values: dict[str, str]) -> dict[str, tuple[None, str]]:
    return {key: (None, value) for key, value in values.items()}


def _mime_type_for_suffix(suffix: str) -> str:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    return "image/png"


def _matches_image_signature(raw: bytes, suffix: str) -> bool:
    if suffix in {"jpg", "jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if suffix == "png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == "webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _contains_provider_url(items: list[Any]) -> bool:
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
            return True
    return False


__all__ = ["IdeogramImagesIntegration"]
