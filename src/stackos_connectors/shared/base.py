"""Provider HTTP retries, rate pacing and cost metadata; no application state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any

import httpx

from stackos_connectors.contracts import RateLimiter
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.redaction import redact_secrets
from stackos_connectors.shared.rate_limit import TokenBucket

if TYPE_CHECKING:
    from stackos_connectors.probe import AuthMethodProbeContext


# Existing provider retry policy: three retries with exponential backoff.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5  # seconds; grows 2x each attempt.

# Bound large provider error bodies before returning diagnostic context.
MAX_LOG_BYTES = 4096

type JsonBody = dict[str, Any] | list[Any]
type FormBody = dict[str, Any]


@dataclass
class IntegrationCallResult:
    """Outcome of a single integration call.

    Provider data, cost and duration are returned to the caller.
    """

    data: Any = field(repr=False)
    cost_usd: float
    duration_ms: int
    cached: bool = False
    metadata: dict[str, Any] | None = field(default=None, repr=False)


class BaseIntegration:
    """Mixin-style base class shared by every integration wrapper."""

    #: Stable provider key used to identify this protocol client.
    kind: str = "unknown"

    #: Default QPS for a caller without an injected rate bucket.
    default_qps: float = 1.0

    #: Vendor short-id used in response metadata.
    vendor: str = "unknown"

    def __init__(
        self,
        *,
        payload: bytes,
        http: httpx.AsyncClient,
        probe_context: AuthMethodProbeContext | None = None,
        rate_limiter: RateLimiter | None = None,
        qps_override: float | None = None,
        timeout: float | None = None,
    ) -> None:
        self.payload = payload
        self._http = http
        self._timeout = timeout
        self.probe_context = probe_context
        self._rate_limiter = (
            rate_limiter
            if rate_limiter is not None
            else TokenBucket.for_qps(qps_override if qps_override is not None else self.default_qps)
        )

    # ------------------------------------------------------------------
    # Hooks for subclasses to override.
    # ------------------------------------------------------------------

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        """Return the *expected* cost of a call before issuing it.

        Used for caller cost estimation. Subclasses with per-op pricing
        override; the default is zero.
        """
        del op, kwargs
        return 0.0

    def _extract_actual_cost_usd(
        self,
        op: str,
        *,
        request: JsonBody | None,
        response: Any,
        estimated: float,
    ) -> float:
        """Reconcile the actual cost from a successful response.

        Some vendors (DataForSEO) report task cost in the response body;
        most do not. Default returns the estimate verbatim.
        """
        del op, request, response
        return estimated

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: JsonBody | None,
        response: Any,
        http_response: httpx.Response,
    ) -> dict[str, Any] | None:
        """Return non-secret provider metadata from a successful response."""
        del op, request, response, http_response
        return None

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _bucket(self) -> RateLimiter:
        return self._rate_limiter

    @staticmethod
    def _truncate(blob: Any) -> Any:
        """Cap a diagnostic payload returned to the caller."""
        if isinstance(blob, dict | list):
            text = repr(blob)
            if len(text) <= MAX_LOG_BYTES:
                return blob
            return {"_truncated": True, "preview": text[:MAX_LOG_BYTES]}
        if isinstance(blob, str) and len(blob) > MAX_LOG_BYTES:
            return blob[:MAX_LOG_BYTES] + "…[truncated]"
        return blob

    @classmethod
    def _sanitize_request(cls, request_json: JsonBody | None) -> JsonBody | None:
        """Strip fields that look like secrets from diagnostic payloads."""
        if not request_json:
            return request_json
        if isinstance(request_json, list):
            return [
                cls._sanitize_request(item) if isinstance(item, dict | list) else item
                for item in request_json
            ]
        sensitive = {
            "api_key",
            "apikey",
            "password",
            "secret",
            "client_secret",
            "authorization",
            "tokenkey",
        }
        clean: dict[str, Any] = {}
        for key, value in request_json.items():
            if key.lower() in sensitive:
                clean[key] = "[redacted]"
            elif isinstance(value, dict | list):
                clean[key] = cls._sanitize_request(value)
            else:
                clean[key] = value
        return clean

    # ------------------------------------------------------------------
    # Core dispatch.
    # ------------------------------------------------------------------

    def _should_retry_response(self, response: httpx.Response) -> bool:
        """Provider response policy; the shared loop still owns retry limits."""
        return response.status_code == 429 or response.status_code >= 500

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        op: str,
        json: JsonBody | None = None,
        data: FormBody | None = None,
        files: Any | None = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        follow_redirects: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> httpx.Response:
        """Issue ``method url`` with rate-limit + retry handling.

        By default, on a non-recoverable HTTP error (4xx other than 429)
        we raise immediately. On 429 / 5xx we retry up to ``max_retries`` times
        with exponential backoff; the final failure surfaces as
        ``IntegrationDownError`` so callers can branch on the typed
        error. Providers may override response retryability, never the limit.
        """
        last_status: int | None = None
        for attempt in range(max_retries + 1):
            await self._bucket().acquire(1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    json=json,
                    data=data,
                    files=files,
                    content=content,
                    params=params,
                    headers=headers,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    **({"timeout": self._timeout} if self._timeout is not None else {}),
                )
            except httpx.HTTPError:
                if attempt >= max_retries:
                    raise IntegrationDownError(
                        f"{self.vendor}.{op} HTTP error after {max_retries} retries",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "retry_after": int(backoff_base * (2**attempt)),
                        },
                    ) from None
                await asyncio.sleep(backoff_base * (2**attempt))
                continue

            last_status = response.status_code
            if response.status_code < 400:
                return response
            should_retry = self._should_retry_response(response)
            if response.status_code == 429:
                # Vendor rate-limited us; honor Retry-After if present.
                retry_after = self._parse_retry_after(response)
                if attempt >= max_retries or not should_retry:
                    raise RateLimitedError(
                        f"{self.vendor}.{op} 429 after {attempt} retries",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "status": response.status_code,
                            "retry_after": retry_after,
                            "provider_error": self._provider_error(response),
                        },
                    )
                wait = retry_after or backoff_base * (2**attempt)
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 500 or should_retry:
                if attempt >= max_retries or not should_retry:
                    raise IntegrationDownError(
                        f"{self.vendor}.{op} status {response.status_code} after {attempt} retries",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "status": response.status_code,
                            "provider_error": self._provider_error(response),
                        },
                    )
                await asyncio.sleep(backoff_base * (2**attempt))
                continue
            # 4xx other than 429 — surface immediately.
            raise IntegrationDownError(
                f"{self.vendor}.{op} client error {response.status_code}",
                data={
                    "vendor": self.vendor,
                    "op": op,
                    "status": response.status_code,
                    "provider_error": self._provider_error(response),
                },
            )

        # Loop completed without return/raise — defensive default.
        # pragma: no cover — loop guarantees one of the above branches fires
        raise IntegrationDownError(
            f"{self.vendor}.{op} retries exhausted",
            data={"vendor": self.vendor, "op": op, "last_status": last_status},
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        """Read a numeric ``Retry-After`` header if present."""
        header = response.headers.get("retry-after")
        if header is None:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = {"message": response.text[:500]}
        return redact_secrets(cls._truncate(body))

    async def call(
        self,
        *,
        op: str,
        method: str = "POST",
        url: str,
        json_body: JsonBody | None = None,
        data_body: FormBody | None = None,
        files: Any | None = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        request_log_body: JsonBody | None = None,
        response_parser: Callable[[httpx.Response], Any] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> IntegrationCallResult:
        """Issue a provider call, preserving its retry/cost/response semantics."""
        if content is not None and any(
            value is not None for value in (json_body, data_body, files)
        ):
            raise ValueError("content cannot be combined with JSON, form data, or files")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        body_for_cost = json_body if json_body is not None else data_body
        request_for_cost = request_log_body if request_log_body is not None else body_for_cost
        estimated = self._estimate_cost_usd(op, json=body_for_cost, params=params)
        started = perf_counter()
        response = await self._request_with_retry(
            method,
            url,
            op=op,
            json=json_body,
            data=data_body,
            files=files,
            content=content,
            params=params,
            headers=headers,
            auth=auth,
            max_retries=max_retries,
        )
        if response_parser is not None:
            data = response_parser(response)
        else:
            try:
                data = response.json()
            except ValueError:
                data = response.text
        actual_cost = self._extract_actual_cost_usd(
            op, request=request_for_cost, response=data, estimated=estimated
        )
        metadata = self._extract_response_metadata(
            op, request=request_for_cost, response=data, http_response=response
        )
        return IntegrationCallResult(
            data=data,
            cost_usd=actual_cost,
            duration_ms=int((perf_counter() - started) * 1000),
            cached=bool(metadata and metadata.get("cached")),
            metadata=metadata,
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Vendor health probe — subclasses override.

        A registered provider must implement its own protocol probe.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement test_credentials")


__all__ = [
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_MAX_RETRIES",
    "MAX_LOG_BYTES",
    "BaseIntegration",
    "FormBody",
    "IntegrationCallResult",
    "JsonBody",
]
