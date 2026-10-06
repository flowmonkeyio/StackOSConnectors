"""HubSpot v3 signature protocol; ingress identity and replay remain caller-owned."""

import base64
import hashlib
import hmac
import re

_V3_URI_DECODES = {
    "%3A": ":",
    "%2F": "/",
    "%3F": "?",
    "%40": "@",
    "%21": "!",
    "%24": "$",
    "%27": "'",
    "%28": "(",
    "%29": ")",
    "%2A": "*",
    "%2C": ",",
    "%3B": ";",
}


def verify_signature_v3(
    secret: str,
    method: str,
    canonical_uri: str,
    timestamp_text: str,
    raw_body: bytes,
    signature: str,
) -> bool:
    """Verify v3 only, using the caller's trusted canonical URI and raw bytes.

    https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation
    """
    signed_uri = canonical_uri
    for encoded, plain in _V3_URI_DECODES.items():
        signed_uri = re.sub(encoded, plain, signed_uri, flags=re.I)
    source = (
        method.encode("utf-8")
        + signed_uri.encode("utf-8")
        + raw_body
        + timestamp_text.encode("utf-8")
    )
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), source, hashlib.sha256).digest()
    ).decode("ascii")
    try:
        return hmac.compare_digest(expected, signature)
    except TypeError:
        return False
