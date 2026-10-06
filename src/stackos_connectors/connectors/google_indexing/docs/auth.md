# Google Indexing authentication

The [bundled catalog](../catalog.json) is the installed contract for method fields,
scopes, endpoints and supported grants. Read it through `describe`; use
`get_auth_contract` for immutable protocol facts.

| Saved method | Explicit token grants |
| --- | --- |
| `service-account` | `jwt_bearer` |

Call `request_token` with resolved `service_account_json` to sign an RSA JWT and
exchange it at `https://oauth2.googleapis.com/token`. The signed request uses only
`https://www.googleapis.com/auth/indexing`. The shared Google key validator rejects
alternate token endpoints and external credential sources. Delegation is not
supported. A successful response requires a Bearer token and a positive expiry;
omitted scope remains omitted provider evidence.

Actions and `probe_credentials` accept the resolved `access_token`. Discovery,
probes and actions never acquire or refresh credentials implicitly. The consumer
owns key and token custody, refresh timing, permissions, persistence and audit.

Token acquisition does not verify API enablement or site-owner access. The service
account needs delegated site-owner access in Search Console. This property role
is separate from Workspace user impersonation. Existing eligibility, quota and
notification semantics remain in the [provider notes](google-indexing.md).

Provider references retained from the reviewed catalog:

- [Indexing prerequisites](https://developers.google.com/search/apis/indexing-api/v3/prereqs)
- [Indexing API usage](https://developers.google.com/search/apis/indexing-api/v3/using-api)
