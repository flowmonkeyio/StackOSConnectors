"""Search Console's explicit read and sitemap-submission OAuth scopes."""

from dataclasses import replace

from ...auth import OAuthTokenError

READ_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"


def resolve_contract(contract, config):
    mode = config.get("access_mode", "readonly")
    if mode not in ("readonly", "sitemap_write"):
        raise OAuthTokenError(
            "Search Console access mode must be readonly or sitemap_write",
            provider_key=contract.provider_key,
            invalid_fields=("access_mode",),
        )
    return replace(contract, scopes=(WRITE_SCOPE if mode == "sitemap_write" else READ_SCOPE,))
