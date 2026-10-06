# Microsoft 365 authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `oauth2_token` | `refresh_token` |

The provider adapter resolves the declared tenant configuration. Authorization-code consent and token exchange remain explicit; no browser, callback server or token store is created.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Related provider notes: [protocol](protocol.md).
