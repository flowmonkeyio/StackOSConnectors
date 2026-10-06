# Google Tag Manager authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `oauth2_access_token` | `refresh_token` |
| `oauth2_refresh_token` | `refresh_token` |
| `service-account` | `jwt_bearer` |

Service-account token success does not establish Tag Manager account/container access. Inventory probes return observed provider facts, including an empty inventory.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Provider references retained from the reviewed catalog (not a new live verification):

- [Provider reference 1](https://developers.google.com/tag-platform/tag-manager/api/v2)
- [Provider reference 2](https://developers.google.com/tag-platform/tag-manager/api/v2/authorization)
- [Provider reference 3](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/list)
- [Provider reference 4](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/list)
- [Provider reference 5](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/snippet)
- [Provider reference 6](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/list)
- [Provider reference 7](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/tags/list)
- [Provider reference 8](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/triggers/list)
