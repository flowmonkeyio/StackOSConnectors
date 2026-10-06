"""Salesforce production, sandbox and My Domain authorization endpoints."""

import re
from dataclasses import replace

from ...auth import OAuthTokenError

_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.my\.salesforce\.com$")


def resolve_contract(contract, config):
    environment = str(config.get("environment") or "production").strip().lower()
    hostname = "login.salesforce.com"
    if environment == "sandbox":
        hostname = "test.salesforce.com"
    elif environment == "my-domain":
        hostname = str(config.get("login_domain") or "").strip().lower()
        if not _DOMAIN_RE.fullmatch(hostname):
            raise OAuthTokenError(
                "Salesforce My Domain must end in .my.salesforce.com",
                provider_key=contract.provider_key,
                invalid_fields=("login_domain",),
            )
    elif environment != "production":
        raise OAuthTokenError(
            "Salesforce environment must be production, sandbox, or my-domain",
            provider_key=contract.provider_key,
            invalid_fields=("environment",),
        )
    base = f"https://{hostname}/services/oauth2"
    return replace(
        contract, authorization_endpoint=f"{base}/authorize", token_endpoint=f"{base}/token"
    )
