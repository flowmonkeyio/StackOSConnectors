"""Native openai images requests and caller-owned media files."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any, ClassVar

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import write_media_file


class OpenAIImagesIntegration(BaseIntegration):
    """Wrapper for ``https://api.openai.com/v1/images/generations``."""

    kind = "openai-images"
    vendor = "openai-images"

    BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-image-2"

    # Official refs:
    # https://developers.openai.com/api/docs/guides/image-generation
    # https://developers.openai.com/api/reference/resources/images
    #
    _GPT_IMAGE_MODELS: ClassVar[frozenset[str]] = frozenset(
        {"gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"}
    )
    _DALL_E_MODELS: ClassVar[frozenset[str]] = frozenset({"dall-e-2", "dall-e-3"})
    # gpt-image-2 always processes input images at high fidelity; the API
    # rejects an explicit input_fidelity parameter for it.
    _INPUT_FIDELITY_MODELS: ClassVar[frozenset[str]] = frozenset(
        {"gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"}
    )
    MAX_PROMPT_CHARS: ClassVar[int] = 32_000
    MAX_EDIT_INPUT_IMAGES: ClassVar[int] = 16
    MAX_EDIT_INPUT_IMAGE_BYTES: ClassVar[int] = 50 * 1024 * 1024
    _OUTPUT_EXTENSIONS: ClassVar[dict[str, str]] = {
        "jpeg": "jpg",
        "png": "png",
        "webp": "webp",
    }
    _INPUT_IMAGE_MIME: ClassVar[dict[str, str]] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8")
        self._output_dir = output_dir
        self.files: list[ConnectorFile] = []
        self.provider_executed = False
        self.submission_http_succeeded = False
        self.generation_outcome: str | None = None
        self.receipt: dict[str, Any] = {}

    def _auth_headers(self, *, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    async def generate(
        self, *, prompt: str, size: str, quality: str, n: int, model: str, output_format: str
    ) -> IntegrationCallResult:
        """Generate ``n`` images from ``prompt``.

        GPT Image models return base64 image bytes. When ``output_dir`` is
        configured, the wrapper writes those bytes to disk and returns a
        response with local ``path`` fields and no ``b64_json`` payloads.
        """
        self._validate_prompt(prompt, model=model)
        body = {
            "prompt": prompt,
            "n": n,
            "model": model,
        }
        # DALL-E keeps legacy size/quality names while GPT Image models use
        # low/medium/high quality and output_format.
        if model in self._DALL_E_MODELS:
            body["size"] = _dalle_size(size)
            body["quality"] = _dalle_quality(quality)
            body["response_format"] = "url"
        else:
            body["size"] = size
            body["quality"] = quality
            body["output_format"] = output_format
        result = await self.call(
            max_retries=0,
            op="generate",
            method="POST",
            url=f"{self.BASE_URL}/images/generations",
            json_body=body,
            headers=self._auth_headers(),
        )
        return IntegrationCallResult(
            data=self._persist_base64_images(
                result.data,
                output_format=output_format,
                model=model,
            ),
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            cached=result.cached,
        )

    async def edit(
        self,
        *,
        prompt: str,
        input_image_paths: list[Path],
        size: str,
        quality: str,
        n: int,
        model: str,
        output_format: str,
        input_fidelity: str | None = None,
    ) -> IntegrationCallResult:
        """Edit/compose images from ``prompt`` plus input reference images.

        GPT Image edits keep the referenced subject (product, logo, label)
        faithful while changing scene, style, or composition. Input images
        are submitted as multipart file uploads. ``input_fidelity`` is
        forwarded only for models that accept it; gpt-image-2 always runs
        at high input fidelity.
        """
        if not input_image_paths:
            raise IntegrationDownError(
                "OpenAI Images edit requires at least one input image",
                data={"vendor": "openai-images", "model": model},
            )
        if len(input_image_paths) > self.MAX_EDIT_INPUT_IMAGES:
            raise IntegrationDownError(
                f"OpenAI Images edit accepts at most {self.MAX_EDIT_INPUT_IMAGES} input images",
                data={"vendor": "openai-images", "model": model},
            )
        self._validate_prompt(prompt, model=model)
        form_body: dict[str, Any] = {
            "prompt": prompt,
            "n": str(n),
            "model": model,
            "size": size,
            "quality": quality,
            "output_format": output_format,
        }
        if input_fidelity is not None and model in self._INPUT_FIDELITY_MODELS:
            form_body["input_fidelity"] = input_fidelity

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for path in input_image_paths:
            filename, raw, mime = self._read_input_image(path)
            files.append(("image", (filename, raw, mime)))
        result = await self.call(
            max_retries=0,
            op="edit",
            method="POST",
            url=f"{self.BASE_URL}/images/edits",
            data_body=form_body,
            files=files,
            headers=self._auth_headers(content_type=None),
        )
        return IntegrationCallResult(
            data=self._persist_base64_images(
                result.data,
                output_format=output_format,
                model=model,
            ),
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            cached=result.cached,
        )

    def _read_input_image(self, path: Path) -> tuple[str, bytes, str]:
        """Read one local input image for multipart upload."""
        mime = self._INPUT_IMAGE_MIME.get(path.suffix.lower())
        if mime is None:
            raise IntegrationDownError(
                "OpenAI Images edit input images must be png, jpg, or webp",
                data={"vendor": "openai-images", "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "OpenAI Images edit input image could not be read",
                data={"vendor": "openai-images", "file": path.name},
            ) from exc
        if len(raw) > self.MAX_EDIT_INPUT_IMAGE_BYTES:
            raise IntegrationDownError(
                "OpenAI Images edit input images must be at most 50 MB",
                data={
                    "vendor": "openai-images",
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": self.MAX_EDIT_INPUT_IMAGE_BYTES,
                },
            )
        return path.name, raw, mime

    def _validate_prompt(self, prompt: str, *, model: str) -> None:
        if len(prompt) > self.MAX_PROMPT_CHARS:
            raise IntegrationDownError(
                "OpenAI Images prompt must be at most 32000 characters",
                data={
                    "vendor": "openai-images",
                    "model": model,
                    "chars": len(prompt),
                    "max_chars": self.MAX_PROMPT_CHARS,
                },
            )

    def _persist_base64_images(
        self,
        data: Any,
        *,
        output_format: str,
        model: str,
    ) -> Any:
        """Replace OpenAI ``b64_json`` entries with caller-local file paths."""
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
            b64 = item.get("b64_json")
            if not isinstance(b64, str):
                persisted.append(item)
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntegrationDownError(
                    "OpenAI Images returned invalid base64 image data",
                    data={"vendor": "openai-images", "model": model},
                ) from exc
            if raw:
                self.generation_outcome = "succeeded"
            ext = self._OUTPUT_EXTENSIONS.get(output_format, "webp")
            clean = {k: v for k, v in item.items() if k != "b64_json"}
            clean.update(
                write_media_file(
                    raw, output_dir=self._output_dir, files=self.files, prefix="openai", ext=ext
                )
            )
            clean["source_model"] = model
            persisted.append(clean)
        out["data"] = persisted
        return out

    async def test_credentials(self) -> dict[str, Any]:
        """Cheap auth probe — list models (free, validates auth)."""
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers=self._auth_headers(),
        )
        models_count = len(result.data.get("data", [])) if isinstance(result.data, dict) else 0
        return {"ok": True, "vendor": "openai-images", "models_count": models_count}

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
            for key in ("id",):
                value = body
                for segment in key.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                if isinstance(value, str) and value:
                    self.receipt.setdefault(key.rsplit(".", 1)[-1], value)
        return response


__all__ = ["OpenAIImagesIntegration"]


def _dalle_quality(quality: str) -> str:
    if quality in {"standard", "hd"}:
        return quality
    return "hd" if quality == "high" else "standard"


def _dalle_size(size: str) -> str:
    if size == "1536x1024":
        return "1792x1024"
    if size == "1024x1536":
        return "1024x1792"
    return size
