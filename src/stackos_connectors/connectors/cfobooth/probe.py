"""One key-only current-identity probe, without password acquisition or refresh."""

import re
from datetime import UTC, datetime

from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError
from stackos_connectors.probe import probe_request

from .config import validate_auth
from .contract import validate_wire
from .transport import execute_request


def _identity(response, org):
    response = validate_wire(response, "current-key-v1")
    identity = response["data"]
    if identity["organizationId"] != org:
        raise ValidationError("CFOBooth probe organization does not match Account")
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
    base, org, key = validate_auth(auth, options)
    request = probe_request("cfobooth", auth, options)
    try:
        _, response, _ = await execute_request(
            request,
            method="GET",
            url=base + f"/api/organizations/{org}/api-keys/current",
            key=key,
            params=[],
        )
        identity = _identity(response, org)
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
                        "organization_id": org,
                        "key_id": identity["keyId"],
                        "expires_at": identity["expiresAt"],
                    },
                }
            }
        },
    }
