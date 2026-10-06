"""Fixed QuickBooks read routes using the shared bounded HTTP transport."""

from __future__ import annotations

import math
from collections.abc import Callable

import httpx

from stackos_connectors.errors import ConnectorError, IntegrationDownError, RateLimitedError
from stackos_connectors.shared.base import BaseIntegration, IntegrationCallResult

from .response import CompanyInfo, InvoicePage

HOSTS = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}


class QuickBooksIntegration(BaseIntegration):
    kind = vendor = "quickbooks-online"

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> dict[str, str]:
        # Never decode/retain arbitrary provider bodies in diagnostics.
        return {"reason_code": _reason(response.status_code)}

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        value = BaseIntegration._parse_retry_after(response)
        return (
            min(value, 30.0) if value is not None and math.isfinite(value) and value >= 0 else None
        )

    async def read(
        self,
        *,
        op: str,
        environment: str,
        realm_id: str,
        token: str,
        path: str,
        parser: Callable[[httpx.Response], CompanyInfo | InvoicePage],
        params: dict[str, str] | None = None,
    ) -> IntegrationCallResult:
        try:
            return await self.call(
                op=op,
                method="GET",
                url=f"{HOSTS[environment]}/v3/company/{realm_id}/{path}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params=params,
                response_parser=parser,
            )
        except (IntegrationDownError, RateLimitedError) as exc:
            status = exc.data.get("status")
            status = status if type(status) is int else None
            raise ConnectorError(
                "QuickBooks read failed",
                provider_status_code=status,
                provider_error={"reason_code": _reason(status)},
            ) from None


def _reason(status: int | None) -> str:
    if status == 401:
        return "authentication_failed"
    if status == 403:
        return "permission_denied"
    if status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "provider_failure" if status is not None else "network_error"
