# QuickBooks Online authentication

The [catalog](../catalog.json) owns fixed Intuit OAuth declarations. Use the public `build_authorization_request`, `request_token` and `get_auth_contract` functions. Neither read action authorizes or refreshes implicitly.

- `oauth2_authorization_code`: explicit code and refresh grants. Consent requests `com.intuit.quickbooks.accounting` with caller-generated state and callback URI.
- `oauth2_token`: resolved bearer-token reads, plus explicit refresh grants when application fields and a refresh token are supplied to `request_token`.
- Both grant requests use HTTP Basic application authentication at `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer`. Consent uses `https://appcenter.intuit.com/connect/oauth2`. The shared grant transport issues one token POST without automatic retries. PKCE is not declared for this provider contract.

The caller owns application secrets, state verification, callback handling, binding the returned `realmId` to the intended company, token custody/expiration, refresh concurrency and permissions. Supply only resolved `access_token` in action auth fields, and `{ "environment": "sandbox", "realm_id": "123456789" }` in action auth config. The environment must be `sandbox` or `production`; endpoints cannot be overridden. Code/token grants accept application fields separately from action execution validation.

An omitted token response scope remains unknown; the requested accounting scope is not evidence of an actual grant. An omitted refresh token remains absent in `TokenResult`; the caller decides whether to retain a prior value. Unknown refresh outcomes require caller reconciliation, not automatic replay. `CompanyInfo.Id` is a separate entity identifier and need not equal callback `realmId`.

Primary protocol sources: [sandbox discovery](https://developer.intuit.com/.well-known/openid_sandbox_configuration/), [production discovery](https://developer.intuit.com/.well-known/openid_configuration/), [official Python OAuth client](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/client.py), and [accounting scope](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/enums.py). Local fixture verification does not establish live app consent or sandbox compatibility.
