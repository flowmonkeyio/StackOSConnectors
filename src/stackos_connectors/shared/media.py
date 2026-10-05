"""Shared media helpers for first-party generation integrations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from stackos_connectors.contracts import ConnectorFile
from stackos_connectors.errors import IntegrationDownError

_OUTPUT_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "application/octet-stream": "mp4",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_UNSAFE_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def write_media_file(
    raw: bytes,
    *,
    output_dir: Path | None,
    prefix: str,
    ext: str,
    files: list[ConnectorFile],
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Write bytes in the caller's directory and append one ordered file receipt.

    The caller owns the directory and passes a collection scoped to its invocation.
    A failed write leaves that collection unchanged and never follows target symlinks.
    """
    if output_dir is None:
        raise ValueError("output_dir is required for generated media")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix) or not re.fullmatch(r"[a-z0-9]+", ext):
        raise ValueError("media prefix and extension must be filename components")
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}-{hashlib.sha256(raw).hexdigest()[:32]}.{ext}"
    target = directory / filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".media-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    descriptor = ConnectorFile(
        path=str(target),
        name=filename,
        mime_type=mime_type or image_mime_type(ext) or ("video/mp4" if ext == "mp4" else None),
        size_bytes=len(raw),
    )
    files.append(descriptor)
    return {"path": str(target), "file_format": ext}


async def download_generated_media(
    integration: Any,
    url: str,
    *,
    fallback_ext: str,
    headers: dict[str, str] | None = None,
    empty_message: str = "provider returned an empty media download",
) -> tuple[bytes, str]:
    """Download temporary provider media and infer a stable file extension."""
    vendor = getattr(integration, "vendor", "unknown")
    current_url = validate_generated_media_url(url, vendor=vendor)
    current_headers = headers
    response = None
    for _ in range(5):
        response = await integration._request_with_retry(
            "GET",
            current_url,
            op="media.download",
            headers=current_headers,
            follow_redirects=False,
        )
        if response.status_code not in _REDIRECT_STATUSES:
            break
        location = response.headers.get("location")
        if not location:
            break
        next_url = validate_generated_media_url(urljoin(current_url, location), vendor=vendor)
        if not _same_origin(current_url, next_url):
            current_headers = None
        current_url = next_url
    if response is None or response.status_code in _REDIRECT_STATUSES:
        raise IntegrationDownError(
            "provider media download returned too many redirects",
            data={"vendor": vendor},
        )
    if not response.content:
        raise IntegrationDownError(
            empty_message,
            data={"vendor": vendor},
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return response.content, media_file_format(content_type, current_url, fallback_ext=fallback_ext)


def validate_generated_media_url(url: str, *, vendor: str) -> str:
    """Reject provider media URLs that would fetch unsafe origins."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme.lower() != "https" or not parsed.netloc or hostname is None:
        raise IntegrationDownError(
            "provider media URL must be an absolute HTTPS URL",
            data={"vendor": vendor},
        )
    if parsed.username is not None or parsed.password is not None:
        raise IntegrationDownError(
            "provider media URL must not include userinfo",
            data={"vendor": vendor},
        )

    normalized_hostname = hostname.rstrip(".").lower()
    if normalized_hostname in _UNSAFE_HOSTNAMES or normalized_hostname.endswith(".localhost"):
        raise IntegrationDownError(
            "provider media URL host is not allowed",
            data={"vendor": vendor},
        )

    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        return url
    if not address.is_global:
        raise IntegrationDownError(
            "provider media URL host is not allowed",
            data={"vendor": vendor},
        )
    return url


def _same_origin(left: str, right: str) -> bool:
    left_parts = urlparse(left)
    right_parts = urlparse(right)
    return (
        left_parts.scheme.lower(),
        left_parts.hostname,
        left_parts.port,
    ) == (
        right_parts.scheme.lower(),
        right_parts.hostname,
        right_parts.port,
    )


def decode_base64_media(
    raw_b64: str,
    *,
    vendor: str,
    error_message: str,
) -> bytes:
    """Decode provider base64 media with a typed integration error."""
    try:
        return base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IntegrationDownError(error_message, data={"vendor": vendor}) from exc


def media_file_format(mime_type: str | None, source_url: str = "", *, fallback_ext: str) -> str:
    """Infer a media extension from MIME type or URL path."""
    if isinstance(mime_type, str):
        clean_mime = mime_type.split(";", 1)[0].strip().lower()
        if clean_mime in _OUTPUT_EXTENSIONS:
            return _OUTPUT_EXTENSIONS[clean_mime]
    suffix = Path(urlparse(source_url).path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp", "mp4"}:
        return "jpg" if suffix == "jpeg" else suffix
    return fallback_ext


def data_url_payload(
    path: Path,
    *,
    allowed_suffixes: frozenset[str],
    max_bytes: int,
    vendor: str,
) -> tuple[str, bytes, str]:
    """Read a generated-asset image and return a provider-ready data URL."""
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in allowed_suffixes:
        raise IntegrationDownError(
            "input media format is not supported by this provider action",
            data={"vendor": vendor, "file": path.name, "suffix": suffix},
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrationDownError(
            "input media could not be read",
            data={"vendor": vendor, "file": path.name},
        ) from exc
    if len(raw) > max_bytes:
        raise IntegrationDownError(
            "input media exceeds provider size limit",
            data={"vendor": vendor, "file": path.name, "bytes": len(raw), "max_bytes": max_bytes},
        )
    mime_type = image_mime_type(suffix)
    if mime_type is None:
        raise IntegrationDownError(
            "input media MIME type could not be inferred",
            data={"vendor": vendor, "file": path.name},
        )
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", raw, mime_type


def image_inline_data(
    path: Path,
    *,
    allowed_suffixes: frozenset[str],
    max_bytes: int,
    vendor: str,
) -> dict[str, Any]:
    """Return Gemini-style inlineData for a local generated image."""
    _, raw, mime_type = data_url_payload(
        path,
        allowed_suffixes=allowed_suffixes,
        max_bytes=max_bytes,
        vendor=vendor,
    )
    return {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(raw).decode("ascii")}}


def image_mime_type(suffix: str) -> str | None:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    if suffix == "webp":
        return "image/webp"
    if suffix == "bmp":
        return "image/bmp"
    return None


__all__ = [
    "data_url_payload",
    "decode_base64_media",
    "download_generated_media",
    "image_inline_data",
    "image_mime_type",
    "media_file_format",
    "validate_generated_media_url",
    "write_media_file",
]
