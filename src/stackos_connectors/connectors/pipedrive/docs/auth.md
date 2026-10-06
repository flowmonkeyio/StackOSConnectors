# Pipedrive authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `oauth2_token` | `refresh_token` |
| `api_token` | Resolved credential only; no token grant declared |

OAuth uses bearer transport. The api_token method uses its distinct provider transport and the trusted company API domain. The read-only current-user probe yields account facts, not invented OAuth grants.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Related provider notes: [protocol](protocol.md).
