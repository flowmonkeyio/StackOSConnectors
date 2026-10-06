"""Company identity probe using the existing validated QuickBooks read action."""

from typing import Any

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import ConnectorError, IntegrationDownError, RateLimitedError
from stackos_connectors.probe import AuthMethodProbeContext

_SAFE_ERROR_REASONS = frozenset(
    {
        "invalid_response",
        "authentication_failed",
        "permission_denied",
        "rate_limited",
        "provider_unavailable",
        "provider_failure",
        "network_error",
    }
)


async def probe(
    *, auth: ConnectorAuth, options: CallOptions, context: AuthMethodProbeContext
) -> dict[str, Any]:
    try:
        result = await get_default_client().execute(
            "quickbooks-online", "quickbooks-online.company-info.get", {}, auth, options
        )
    except ConnectorError as exc:
        data: dict[str, Any] = {"stage": "test", "reason_code": "probe_error"}
        reason = (
            exc.provider_error.get("reason_code") if isinstance(exc.provider_error, dict) else None
        )
        if isinstance(reason, str) and reason in _SAFE_ERROR_REASONS:
            data["reason_code"] = reason
        status = exc.provider_status_code
        if type(status) is int and 100 <= status <= 599:
            data["status"] = status
        error_type = RateLimitedError if status == 429 else IntegrationDownError
        raise error_type("QuickBooks CompanyInfo probe failed.", data=data) from None

    company = result.output_json
    return {
        "ok": True,
        "vendor": "quickbooks-online",
        "status": "ok",
        "summary": "QuickBooks CompanyInfo read succeeded for the configured realm.",
        "metadata": {
            "evidence": {
                "account": {
                    # CompanyInfo.Id is an entity id, not the account's realm id.
                    "provider_account_id": None,
                    "display_name": company.get("company_name"),
                    "metadata": {
                        "company_id": company["company_id"],
                        "configured_realm_id": company["realm_id"],
                        "environment": auth.config["environment"],
                    },
                }
            }
        },
    }
