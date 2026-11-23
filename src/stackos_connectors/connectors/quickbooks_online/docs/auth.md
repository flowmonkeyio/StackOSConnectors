# QuickBooks Online authentication

The [catalog](../catalog.json) owns fixed Intuit OAuth declarations. Use the public `build_authorization_request`, `request_token`, `revoke_token` and `get_auth_contract` functions. No read action authorizes, refreshes or revokes implicitly.

- `oauth2_authorization_code`: explicit code and refresh grants. Consent requests `com.intuit.quickbooks.accounting` with caller-generated state and callback URI.
- `oauth2_token`: resolved bearer-token reads, plus explicit refresh grants when application fields and a refresh token are supplied to `request_token`.
- Both grant requests use HTTP Basic application authentication at `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer`. Consent uses `https://appcenter.intuit.com/connect/oauth2`. The shared grant transport issues one token POST without automatic retries. PKCE is not declared for this provider contract.

The caller owns application secrets, state verification, callback handling, binding the returned `realmId` to the intended company, token custody/expiration, refresh concurrency and permissions. Supply only resolved `access_token` in action auth fields, and `{ "environment": "sandbox", "realm_id": "123456789" }` in action auth config. The environment must be `sandbox` or `production`; endpoints cannot be overridden. Code/token grants accept application fields separately from action execution validation.

Code and refresh grants preserve provider-supplied `x_refresh_token_expires_in` and `x_refresh_token_hard_expires_in` in `TokenResult.metadata`. Missing values stay absent; no lifetime is invented and no hard-expiry opt-in header is added. Metadata values are provider evidence, not validated timestamps: the caller must validate their type and range, derive conservative expiry from request start, retain known hard ceilings and atomically store expiry with any rotated refresh token.

An omitted token response scope remains unknown; the requested accounting scope is not evidence of an actual grant. An omitted refresh token remains absent in `TokenResult`; the caller decides whether to retain a prior value. Unknown refresh outcomes require caller reconciliation, not automatic replay. `CompanyInfo.Id` is a separate entity identifier and need not equal callback `realmId`.

## Explicit disconnection

`revoke_token("quickbooks-online", auth=application_auth, token=latest_refresh_token)`
supports both declared OAuth methods. Supply `client_id` and `client_secret` in
application auth fields and the exact latest persisted refresh token explicitly.
The helper does not select a token from auth fields or refresh before revoking.
It sends one HTTP Basic-authenticated JSON POST (`{"token": supplied_value}`) to
the fixed `https://developer.api.intuit.com/v2/oauth2/tokens/revoke` endpoint,
shared by sandbox and production. Caller configuration cannot replace this URL.

Only HTTP 200 confirms revocation and returns `None`. The response body is not
read or decoded, so an empty success is supported and unneeded provider content
cannot be buffered or exposed. Other statuses, including 204, redirects and 400,
raise sanitized `OAuthTokenError`; a generic 400 is not an already-revoked success.
Requests have a positive finite timeout (30 seconds by default), never follow
redirects and never retry automatically. A caller-supplied HTTP client remains
open; the helper closes its own client and every response.

The caller owns authorization, grant-wide concurrency across duplicate company
connections, encrypted custody, local cleanup and recovery. A timeout or lost
response can leave the remote outcome unknown. A transient error classification
does not establish safe replay or authorize a new grant; reconcile through the
calling application's lifecycle before retrying. Unit and installed-package
transport fixtures do not prove live Intuit disconnection.

Both method setup forms require an explicit environment and numeric company realm
ID of 1–32 digits. Neither field has a default. The caller validates the intended
realm against the OAuth callback before exchanging a code.

`probe_credentials` reuses `quickbooks-online.company-info.get` with the resolved
token and configured realm. It returns optional company display name and metadata
containing provider-returned `company_id`, caller-configured `configured_realm_id`
and `environment`. `provider_account_id` remains null because the returned entity
ID is not realm attestation. No grants or scopes are inferred from a successful
CompanyInfo read. Malformed responses and credential echoes fail through the same
validator as the action; provider failures expose only safe status/reason facts.

Primary protocol sources: [sandbox discovery](https://developer.intuit.com/.well-known/openid_sandbox_configuration/), [production discovery](https://developer.intuit.com/.well-known/openid_configuration/), [official Python OAuth client](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/client.py), and [accounting scope](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/enums.py). Local fixture verification does not establish live app consent or sandbox compatibility.
