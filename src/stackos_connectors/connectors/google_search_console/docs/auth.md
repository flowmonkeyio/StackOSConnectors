# Google Search Console authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `oauth2_authorization_code` | `authorization_code`, `refresh_token` |
| `oauth2_access_token` | `refresh_token` |
| `oauth2_refresh_token` | `refresh_token` |
| `service-account` | `jwt_bearer` |

All four methods expose `access_mode`: omitted or `readonly` requests
`https://www.googleapis.com/auth/webmasters.readonly`; explicit `sitemap_write`
requests `https://www.googleapis.com/auth/webmasters`. Keep the saved selection
when renewing a service-account token. Changing it requires fresh grant evidence;
OAuth consent must be repeated when a new scope is needed. Selecting a mode does
not expand a manually supplied token's permissions.

The full `webmasters` scope also permits read operations. Consumers can use
`stackos_connectors.auth.scope_satisfies` to check that documented relationship
without changing stored provider scope evidence. Read-only scope never satisfies
a sitemap submission requirement. Property permissions remain separate.

Service-account token success does not establish Search Console property access. Inventory probes can succeed with no properties. Google Indexing has a separate [service-account authentication contract](../../google_indexing/docs/auth.md) and scope.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Related provider notes: [google-search-console](google-search-console.md).

Provider references retained from the reviewed catalog (not a new live verification):

- [Provider reference 1](https://developers.google.com/webmaster-tools/v1/quickstart/quickstart-python)
- [Provider reference 2](https://support.google.com/webmasters/answer/7687615)
- [Provider reference 3](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing)
- [Provider reference 4](https://developers.google.com/webmaster-tools/v1/sites/list)
- [Provider reference 5](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
- [Provider reference 6](https://developers.google.com/webmaster-tools/v1/sitemaps/list)
- [Provider reference 7](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
