"""Microsoft's documented tenant-specific OAuth endpoints."""

import re
from dataclasses import replace

from ...auth import OAuthTokenError

_TENANT_RE = re.compile(r"^(?:common|organizations|consumers|[0-9a-fA-F-]{36}|[A-Za-z0-9.-]+)$")


def resolve_contract(contract, config):
    tenant = str(config.get("tenant") or "common").strip()
    if not _TENANT_RE.fullmatch(tenant) or ".." in tenant:
        raise OAuthTokenError(
            "Microsoft tenant must be common, organizations, consumers, a tenant id, "
            "or a verified tenant domain",
            provider_key=contract.provider_key,
            invalid_fields=("tenant",),
        )
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return replace(
        contract, authorization_endpoint=f"{base}/authorize", token_endpoint=f"{base}/token"
    )
