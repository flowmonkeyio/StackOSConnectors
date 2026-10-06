# Slack v0 request signatures

`stackos_connectors.connectors.slack_bot.auth.verify_signature_v0(secret,
timestamp_text, raw_body, signature)` verifies Slack's HMAC-SHA256 signature of
the original request bytes. It returns a boolean and makes no network calls.

The consumer resolves the signing secret, parses and enforces timestamp/replay
limits, binds the request to its project/profile, and applies ingress policy.
This helper does not decide whether a correctly signed request should be accepted.

[Slack request signing](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
