"""One key-only current-identity probe, without password acquisition or refresh."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime

from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError
from stackos_connectors.probe import probe_request

from .config import validate_credential
from .contract import validate_wire
from .transport import execute_request


def project_config(config: Mapping[str, object]) -> dict[str, object]:
    """Project credential setup only, leaving scope discovery to the server."""
    return {"api_base_url": config["api_base_url"]} if "api_base_url" in config else {}


def _identity(response):
    response = validate_wire(response, "current-key-v1")
    identity = response["data"]
    expires = identity["expiresAt"]
    if expires is not None:
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", expires):
            raise ValidationError("invalid CFOBooth key expiration")
        try:
            expiration = datetime.fromisoformat(expires)
        except ValueError:
            raise ValidationError("invalid CFOBooth key expiration") from None
        if expiration <= datetime.now(UTC):
            raise ValidationError("CFOBooth key has expired")
    return identity


async def probe(*, auth, options, context):
    base, key = validate_credential(auth, options)
    request = probe_request("cfobooth", auth, options)
    try:
        _, response, _ = await execute_request(
            request,
            method="GET",
            url=base + "/api/api-keys/current",
            key=key,
            params=[],
        )
        identity = _identity(response)
    except (ConnectorError, ValidationError) as exc:
        raise IntegrationDownError(
            "CFOBooth key identity could not be verified",
            data={
                "stage": "test",
                "reason_code": "invalid_key_identity",
                **({"status": exc.provider_status_code} if exc.provider_status_code else {}),
            },
        ) from None
    return {
        "ok": True,
        "vendor": "cfobooth",
        "status": "ok",
        "api_base_url": base,
        "identity": identity,
        "metadata": {
            "evidence": {
                "account": {
                    "provider_account_id": identity["user"]["id"],
                    "display_name": identity["user"]["email"],
                    "metadata": {
                        "organization_id": identity["organizationId"],
                        "key_id": identity["keyId"],
                        "expires_at": identity["expiresAt"],
                    },
                }
            }
        },
    }
