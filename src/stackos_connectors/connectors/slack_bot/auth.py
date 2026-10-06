"""Slack request-signature protocol, independent of ingress and replay policy."""

import hashlib
import hmac


def verify_signature_v0(secret: str, timestamp_text: str, raw_body: bytes, signature: str) -> bool:
    """Verify original request bytes; callers validate time and resolve the secret.

    https://docs.slack.dev/authentication/verifying-requests-from-slack/
    """
    basestring = b"v0:" + timestamp_text.encode("utf-8") + b":" + raw_body
    digest = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(f"v0={digest}", signature)
    except TypeError:
        return False
