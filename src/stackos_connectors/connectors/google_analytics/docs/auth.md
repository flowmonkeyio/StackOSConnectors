# Google Analytics 4 authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `oauth2_access_token` | `refresh_token` |
| `oauth2_refresh_token` | `refresh_token` |
| `service-account` | `jwt_bearer` |

Service-account token success does not establish Analytics account/property access. Inventory probes return observed provider facts, including an empty inventory.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Provider references retained from the reviewed catalog (not a new live verification):

- [Provider reference 1](https://developers.google.com/analytics/devguides/reporting/data/v1)
- [Provider reference 2](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport)
- [Provider reference 3](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/getMetadata)
- [Provider reference 4](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runRealtimeReport)
- [Provider reference 5](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list)
