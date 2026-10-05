"""Native kling video requests and caller-owned media files."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar

import httpx

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult
from stackos_connectors.shared.media import (
    data_url_payload,
    download_generated_media,
    write_media_file,
)


class KlingVideoIntegration(BaseIntegration):
    """Wrapper for Kling text-to-video and image-to-video tasks."""

    kind = "kling"
    vendor = "kling"

    BASE_URL = "https://api-singapore.klingai.com"
    DEFAULT_MODEL = "kling-v3"
    MODELS: ClassVar[frozenset[str]] = frozenset({"kling-v3"})
    TEXT_MODELS: ClassVar[frozenset[str]] = frozenset({"kling-v3"})
    QUALITY_MODES: ClassVar[frozenset[str]] = frozenset({"std", "pro", "4k"})
    ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset({"16:9", "9:16", "1:1"})
    SOUND_VALUES: ClassVar[frozenset[str]] = frozenset({"on", "off"})
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png"})
    MAX_INPUT_IMAGE_BYTES = 10 * 1024 * 1024
    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"succeed", "failed"})

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
            "Authorization": f"Bearer {self._jwt_token()}",
            "Content-Type": "application/json",
        }

    async def generate_video(
        self,
        *,
        prompt: str,
        mode: str,
        model_name: str,
        quality_mode: str,
        duration: int,
        aspect_ratio: str,
        sound: str,
        negative_prompt: str | None = None,
        cfg_scale: float | None = None,
        input_image_path: Path | None = None,
        image_tail_path: Path | None = None,
        watermark_enabled: bool | None = None,
        external_task_id: str | None = None,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        endpoint = "text2video" if mode == "text-to-video" else "image2video"
        body = self._build_request_body(
            prompt=prompt,
            endpoint=endpoint,
            model_name=model_name,
            quality_mode=quality_mode,
            duration=duration,
            aspect_ratio=aspect_ratio,
            sound=sound,
            negative_prompt=negative_prompt,
            cfg_scale=cfg_scale,
            input_image_path=input_image_path,
            image_tail_path=image_tail_path,
            watermark_enabled=watermark_enabled,
            external_task_id=external_task_id,
        )
        submitted = await self.call(
            max_retries=0,
            op="video.generate",
            method="POST",
            url=f"{self.BASE_URL}/v1/videos/{endpoint}",
            json_body=body,
            headers=self._auth_headers(),
        )
        self._raise_for_service_error(submitted.data, op="video.generate")
        task_id = self._task_id(submitted.data)
        poll_result = await self._poll_task(
            endpoint=endpoint,
            task_id=task_id,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        persisted = await self._persist_task_response(
            poll_result.data,
            task_id=task_id,
            model_name=model_name,
            mode=mode,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=submitted.cost_usd + poll_result.cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    def _build_request_body(
        self,
        *,
        prompt: str,
        endpoint: str,
        model_name: str,
        quality_mode: str,
        duration: int,
        aspect_ratio: str,
        sound: str,
        negative_prompt: str | None,
        cfg_scale: float | None,
        input_image_path: Path | None,
        image_tail_path: Path | None,
        watermark_enabled: bool | None,
        external_task_id: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model_name": model_name,
            "prompt": prompt,
            "duration": str(duration),
            "mode": quality_mode,
            "sound": sound,
        }
        if endpoint == "text2video":
            body["aspect_ratio"] = aspect_ratio
        else:
            if input_image_path is None and image_tail_path is None:
                raise IntegrationDownError(
                    "Kling image-to-video requires image or image_tail",
                    data={"vendor": self.vendor, "model": model_name},
                )
            if input_image_path is not None:
                body["image"] = self._raw_image_base64(input_image_path)
            if image_tail_path is not None:
                body["image_tail"] = self._raw_image_base64(image_tail_path)
        if negative_prompt is not None:
            body["negative_prompt"] = negative_prompt
        if cfg_scale is not None:
            body["cfg_scale"] = cfg_scale
        if watermark_enabled is not None:
            body["watermark_info"] = {"enabled": watermark_enabled}
        if external_task_id is not None:
            body["external_task_id"] = external_task_id
        return body

    async def _poll_task(
        self,
        *,
        endpoint: str,
        task_id: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=f"{self.BASE_URL}/v1/videos/{endpoint}/{task_id}",
                headers=self._auth_headers(),
            )
            self._raise_for_service_error(poll_result.data, op="video.poll")
            task = self._task_node(poll_result.data)
            status = str(task.get("task_status") or "").lower()
            if status in self.TERMINAL_STATUSES:
                self.generation_outcome = "succeeded" if status == "succeed" else "failed"
                if status == "succeed":
                    return poll_result
                raise IntegrationDownError(
                    "Kling video generation failed",
                    data={
                        "vendor": self.vendor,
                        "task_id": task_id,
                        "status": status,
                        "message": task.get("task_status_msg"),
                    },
                )
            await asyncio.sleep(poll_interval_seconds)
        raise IntegrationDownError(
            "Kling video generation timed out",
            data={"vendor": self.vendor, "task_id": task_id},
        )

    async def _persist_task_response(
        self,
        data: Any,
        *,
        task_id: str,
        model_name: str,
        mode: str,
    ) -> dict[str, Any]:
        task = self._task_node(data)
        result = task.get("task_result")
        videos = result.get("videos") if isinstance(result, dict) else None
        if not isinstance(videos, list) or not videos:
            raise IntegrationDownError(
                "Kling task completed without generated videos",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        if self._output_dir is None:
            return data if isinstance(data, dict) else {"data": data}
        persisted: list[dict[str, Any]] = []
        for index, video in enumerate(videos):
            if not isinstance(video, dict) or not isinstance(video.get("url"), str):
                raise IntegrationDownError(
                    "Kling generated video entry did not include a URL",
                    data={"vendor": self.vendor, "task_id": task_id, "index": index},
                )
            raw, ext = await download_generated_media(
                self,
                str(video["url"]),
                fallback_ext="mp4",
                empty_message="Kling returned an empty video download",
            )
            item = {
                **{key: value for key, value in video.items() if key != "url"},
                **write_media_file(
                    raw,
                    output_dir=self._output_dir,
                    files=self.files,
                    prefix="kling-video",
                    ext=ext,
                ),
                "source_model": model_name,
                "task_id": task_id,
                "mode": mode,
                "sample_index": index,
            }
            persisted.append(item)
        return {
            "task_id": task_id,
            "request_id": str(data.get("request_id") or "") if isinstance(data, dict) else "",
            "status": str(task.get("task_status") or "succeed"),
            "model": model_name,
            "data": persisted,
            "usage": {
                "final_unit_deduction": task.get("final_unit_deduction"),
            },
        }

    def _jwt_token(self) -> str:
        access_key, secret_key = self._credential_pair()
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}
        signing_input = ".".join(
            [
                _base64url_json(header),
                _base64url_json(payload),
            ]
        )
        digest = hmac.new(
            secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{signing_input}.{_base64url_bytes(digest)}"

    def _credential_pair(self) -> tuple[str, str]:
        raw = self.payload.decode("utf-8").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            access_key = parsed.get("access_key") or parsed.get("ak")
            secret_key = parsed.get("secret_key") or parsed.get("sk")
            if isinstance(access_key, str) and isinstance(secret_key, str):
                return access_key, secret_key
        if ":" in raw:
            access_key, secret_key = raw.split(":", 1)
            if access_key and secret_key:
                return access_key, secret_key
        raise IntegrationDownError(
            "Kling credential must be JSON with access_key and secret_key",
            data={"vendor": self.vendor},
        )

    def _raw_image_base64(self, path: Path) -> str:
        data_url, _, _ = data_url_payload(
            path,
            allowed_suffixes=self.INPUT_IMAGE_FORMATS,
            max_bytes=self.MAX_INPUT_IMAGE_BYTES,
            vendor=self.vendor,
        )
        return data_url.split(",", 1)[1]

    @staticmethod
    def _task_id(data: Any) -> str:
        task = KlingVideoIntegration._task_node(data)
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise IntegrationDownError(
                "Kling response did not return a task_id",
                data={"vendor": KlingVideoIntegration.vendor},
            )
        return task_id

    @staticmethod
    def _task_node(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise IntegrationDownError(
                "Kling returned a non-JSON response",
                data={"vendor": KlingVideoIntegration.vendor},
            )
        node = data.get("data")
        if isinstance(node, dict):
            return node
        return data

    def _raise_for_service_error(self, data: Any, *, op: str) -> None:
        if not isinstance(data, dict):
            return
        code = data.get("code")
        if code in (None, 0, "0"):
            return
        if op == "video.generate":
            self.generation_outcome = "failed"
        raise IntegrationDownError(
            f"Kling {op} returned service code {code}",
            data={
                "vendor": KlingVideoIntegration.vendor,
                "op": op,
                "code": code,
                "message": data.get("message"),
                "request_id": data.get("request_id"),
            },
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/v1/videos/text2video",
            params={"pageNum": 1, "pageSize": 1},
            headers=self._auth_headers(),
        )
        self._raise_for_service_error(result.data, op="test")
        return {"ok": True, "vendor": self.vendor}

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
            for key in ("request_id", "data.task_id"):
                value = body
                for segment in key.split("."):
                    value = value.get(segment) if isinstance(value, dict) else None
                if isinstance(value, str) and value:
                    self.receipt.setdefault(key.rsplit(".", 1)[-1], value)
        return response


def _base64url_json(value: dict[str, Any]) -> str:
    return _base64url_bytes(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _base64url_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


__all__ = ["KlingVideoIntegration"]
