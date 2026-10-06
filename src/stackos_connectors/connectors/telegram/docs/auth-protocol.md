# Telegram authorization protocol helpers

`stackos_connectors.connectors.telegram.auth` translates an explicit caller's
authorization inputs into TDLib dictionaries. `initial_request` builds a bot
token or QR request (phone sign-in starts without a request); `challenge_request`
builds the phone, code, password, email or registration response. These returned
dictionaries contain sensitive values: send them directly and do not log or
persist them.

`parse_authorization_state` returns immutable provider facts: state category,
challenge kind/fields, safe metadata, and a separate `qr_link` excluded from
`repr`. `CHALLENGE_FIELDS` is immutable; `sanitize_challenge_metadata` applies the
same allowlist to restored challenge metadata. `is_saved_authorization_rejected`
classifies explicit allowlisted TDLib errors, without choosing a recovery action.

These functions do not start a client, send a request, restore a session, read
credentials, persist state, or decide readiness. The consumer owns native client
and session lifetime, encryption, generation fencing, challenge expiry, local QR
disclosure, and credential invalidation. Provider timeouts are facts; the consumer
applies its own expiry limit. StackOS keeps its existing 600-second cap.

Provider reference: [TDLib authorization states](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1_authorization_state.html).
