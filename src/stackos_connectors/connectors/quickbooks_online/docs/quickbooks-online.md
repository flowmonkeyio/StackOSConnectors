# QuickBooks Online bounded reads

`get_default_client().execute` exposes exactly two actions:

| Action | Inputs | Output |
| --- | --- | --- |
| `quickbooks-online.company-info.get` | Empty object | Configured `realm_id`, provider `company_id`, optional `company_name` |
| `quickbooks-online.invoices.list` | `txn_date_from`, `txn_date_to`, `start_position`, `max_results` | `invoices`, `start_position`, `max_results`, `count`, optional provider `total_count` |

Use `ConnectorAuth("oauth2_token", {"access_token": token}, config={"environment": "sandbox", "realm_id": realm_id})` and optional `CallOptions(http=caller_http, timeout=seconds, rate_limiter=caller_bucket)`. The caller owns an injected HTTP client; the connector closes only clients it creates. The numeric timeout overrides each HTTP request; a consumer that needs a whole-operation deadline must enforce it separately. Shared transport retries reads at most three times. Redirects are disabled even when a supplied client enables them. Numeric Retry-After is capped at 30 seconds per retry. OAuth renewal uses its separate explicit, non-retrying token transport.

Invoice queries use inclusive valid calendar dates on `TxnDate` and provider-order offset pagination. The query uses `STARTPOSITION` and `MAXRESULTS`, matching the [current official PHP SDK query builder](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/QueryFilter/QueryMessage.php). Explicit ordering is omitted because this increment does not require it and Invoice `Id` sort support was not established by primary evidence. Dates must be ordered, start position is 1-based (maximum 2147483647), and page size is 1–100. There is no arbitrary SQL, URL, write, or minor-version override. Sandbox requests use `sandbox-quickbooks.api.intuit.com`; production requests use `quickbooks.api.intuit.com`.

Every invoice carries `native_id`, exact UTF-8 `raw_json`, optional `source_revision` from supplied `SyncToken` and optional `source_updated_at` from supplied offset-aware `MetaData.LastUpdatedTime`. Object text is sliced from the response, retaining whitespace, escaping, numeric precision and scale. Monetary numbers are validated with decimal decoding and are never decoded as binary floats or reserialized. Supplied totals/balances may be zero or negative. Missing currency and other optional fields remain missing; no currency is inferred. Amounts and balances describe the provider's current response, not historical balances or revenue.

The provider validates the returned envelope and metadata because the public output schema is descriptive. Duplicate JSON keys anywhere, duplicate invoice IDs in a page, invalid identities/amounts/dates, invoices outside the requested dates, inconsistent positions/counts, malformed metadata, records above 16 KiB and response bodies above 2 MiB fail safely. The response-size check occurs after shared HTTP buffering. Credential echoes in raw text or decoded keys/values fail before registry redaction; evidence is never silently scrubbed and returned as exact.

A missing `Invoice` array in an otherwise empty `QueryResponse` means an empty page. Empty pages may omit position/count metadata; returned position then repeats the requested position and count is zero. Nonempty pages require `startPosition` and `maxResults` matching the request and returned count. Supplied `totalCount` is retained only as a nonnegative integer; its semantics are not used for termination.

The consumer owns page traversal, cross-page duplicate detection, aggregate size/count bounds, empty-scan handling, overflow detection, persistence and publication. A short page can terminate a declared traversal, but offset pagination is not a transactionally isolated snapshot. The returned `realm_id` repeats request context, not independent provider attestation. Provider faults return fixed safe error codes and status, never raw response bodies, tokens or transport exception text.

The explicit credential probe calls the existing CompanyInfo action and shares
its validation, transport, retry bounds and redaction. It reports only safe company
identity, keeps configured realm context distinct from `CompanyInfo.Id`, and does
not establish OAuth scope grants. See [authentication](auth.md) for its result.

Run local checks with `.venv/bin/python -B -m pytest tests/quickbooks_online tests/auth tests/catalog tests/packaging -p no:cacheprovider` and `.venv/bin/ruff check src/stackos_connectors/connectors/quickbooks_online tests/quickbooks_online`. The standalone installed-wheel driver in `tests/packaging/installed_auth_consumer.py` uses injected transports with live networking prohibited. Live Intuit consent/provider verification is a separate gate.

References: [Invoice API](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/invoice), [limits and throttling](https://static.developer.intuit.com/output_html/qbo/docs/learn/limits-and-throttles.html), [authentication](auth.md).
