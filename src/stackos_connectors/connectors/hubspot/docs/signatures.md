# HubSpot v3 request signatures

`stackos_connectors.connectors.hubspot.signature.verify_signature_v3(secret,
method, canonical_uri, timestamp_text, raw_body, signature)` verifies the base64
HMAC-SHA256 signature using HubSpot's selective URI decoding. Only v3 is supported.
The helper uses the original body bytes and returns a boolean without network calls.

The consumer resolves the secret and trusted canonical public URI, parses the
timestamp, enforces replay limits, binds the project/account, and applies ingress
policy. Untrusted request host headers must not choose the canonical URI.

[HubSpot request validation](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation)
