# Reddit authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields, scopes, endpoints and supported grants. Read it through `describe`; use `get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `client_credentials` | `client_credentials` |

This provider uses explicit HTTP Basic client-credentials requests. The caller supplies the required user_agent alongside application fields.

`request_token` receives resolved application/key fields and performs only the requested grant. Actions and `probe_credentials` receive resolved execution fields. Discovery, probes and actions never acquire or refresh implicitly. The consumer owns credential/state/PKCE custody, callbacks, refresh timing and concurrency, permissions, persistence and audit. Token results and authorization URLs are sensitive in-process values.

Provider references retained from the reviewed catalog (not a new live verification):

- [Provider reference 1](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- [Provider reference 2](https://redditinc.com/policies/data-api-terms)
- [Provider reference 3](https://github.com/reddit-archive/reddit/wiki/oauth2)
