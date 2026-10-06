# HubSpot authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `private_app_token` | Resolved credential only; no token grant declared |

OAuth supports declared optional scope bundles and the versioned token endpoint. Private-app tokens use their separate token-information probe. Pure ingress verification implements v3 only; callers enforce trusted URI, timestamp/replay and routing policy.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Related provider notes: [hubspot](hubspot.md), [signatures](signatures.md).
