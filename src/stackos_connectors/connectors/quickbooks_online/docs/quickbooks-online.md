# QuickBooks Online bounded reads

`get_default_client().execute` exposes three read actions:

| Action | Inputs | Output |
| --- | --- | --- |
| `quickbooks-online.company-info.get` | Empty object | Configured `realm_id`, provider `company_id`, optional `company_name` |
| `quickbooks-online.records.list` | Closed `entity` plus scope-dependent fields below | `entity`, `records`, `count`; pagination metadata for queries only |
| `quickbooks-online.invoices.list` | `txn_date_from`, `txn_date_to`, `start_position`, `max_results` | `invoices`, `start_position`, `max_results`, `count`, optional provider `total_count` |

## Accounting record contract

`records.list` admits exactly these entities:

| Read family | Entities | Required input fields |
| --- | --- | --- |
| Transaction | Invoice, Payment, SalesReceipt, RefundReceipt, CreditMemo, Bill, BillPayment, Purchase, VendorCredit, Deposit, Transfer, JournalEntry, Estimate, PurchaseOrder, CreditCardPayment, InventoryAdjustment | `entity`, `txn_date_from`, `txn_date_to`, `start_position`, `max_results` |
| Reference | Account, Customer, Vendor, Employee, Item, Class, Department, PaymentMethod, Term, TaxCode, TaxRate, TaxAgency | `entity`, `start_position`, `max_results` |
| Singleton | CompanyInfo, Preferences | `entity` only |

The schema rejects extra properties, unknown entities, transaction dates on
reference reads and pagination on singleton reads. Dates must be real ordered
calendar dates, positions are 1–2147483647 and page sizes are 1–100.
Integral JSON numbers such as `1.0` are normalized to native integers before
query construction and page parsing; fractions and booleans are rejected.

Every output contains the requested `entity`, `records` and `count`. Each record
contains `native_id`, exact `raw_json`, optional `source_revision` and optional
`source_updated_at`. Query outputs additionally contain `start_position`,
`max_results` (returned record count), and optional provider `total_count`.
Singleton outputs have exactly one record and omit all pagination fields.
Every native record, including settings, must supply its own nonempty `Id`.
Missing optional native fields stay missing. Present declared scalar/reference
fields must have their native types; there is no default currency or zero fill.
Nested lines, links and all other provider fields remain unchanged raw evidence.

Transaction reads use their fixed entity identifier and inclusive `TxnDate`
predicate. `CreditCardPayment` alone uses the SDK's `creditcardpayment` query
identifier; its only accepted response keys are `CreditCardPayment` and
`CreditCardPaymentTxn`, never both. InventoryAdjustment uses its native entity
name and inherited transaction date. Reference reads have no date predicate.
Reference queries except TaxAgency, TaxCode and TaxRate use `Active IN (true, false)`.
TaxAgency has no Active property. TaxCode and TaxRate use unfiltered paginated
queries because the predicate is rejected in the retained sandbox (HTTP 400,
Fault 4001). Their supplied Active values remain validated and preserved; no
client-side activity filter or default is applied. This does not establish
complete inactive coverage across every tax model.
CompanyInfo uses `companyinfo/{realm_id}`; Preferences uses `preferences`, with
no object-ID suffix. No caller query, path, URL or minor version is accepted.

Estimate and PurchaseOrder are non-posting documents. Payment/credit/transfer
records and journal line sides remain distinct native facts; this connector
never declares transaction totals to be expenses or revenue. Employee is an
accounting reference, not a payroll API. Payroll, Payments, Time, Desktop,
attachments/PDFs, budgets, recurring templates, projects/change orders,
specialized tax workflows, Reports API ingestion and all writes are excluded.
This catalog is broad core coverage, not all QuickBooks products or complete books.

The shared raw parser bounds the complete HTTP response at 2 MiB for every
entity. There is no separate per-record size cutoff; valid native records retain
their exact raw text within that aggregate budget. Wrong envelopes, ambiguous aliases, duplicate JSON keys/IDs,
malformed supplied metadata and credential echoes fail safely. A missing entity
array in an otherwise empty query envelope means an empty page. Provider faults,
denials or unsupported features are errors, never empty successful collections.
Consumer code owns cross-page consistency, acquisition limits and publication.
A complete offset traversal is not a provider-guaranteed point-in-time snapshot;
absence on a later traversal does not by itself prove deletion.

The local source and installed-distribution tests use injected transports and
synthetic records for all 30 entities. They do not establish live availability,
permissions or region/plan support. A separate authorized sandbox probe on
2026-10-12 UTC used the installed 0.2.6 candidate with bounded one-record reads.
The explicit Active filters were accepted for Employee, Department,
PaymentMethod, TaxCode and TaxRate; Department returned an empty page and the
other four each returned one record. CreditCardPayment accepted the dated query
and returned an empty page. InventoryAdjustment returned one record. CompanyInfo
and Preferences each returned one native singleton. These reads establish query
acceptance and the populated envelopes observed, not actual inactive-record
retrieval or a populated CreditCardPayment response. Published-package product
integration and complete source-to-consumer reconciliation remain separate proof.

Primary contract sources: Intuit's [query guide](https://medium.com/intuitdev/deep-dive-into-quickbooks-online-data-queries-b77034bdc144),
[SDK singleton routes](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/DataService/DataService.php),
[CreditCardPayment query mapping](https://github.com/intuit/QuickBooks-V3-DotNET-SDK/blob/master/IPPDotNetDevKitCSV3/Code/Intuit.Ipp.DataService/DataService.cs),
[InventoryAdjustment inheritance](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/Data/IPPInventoryAdjustment.php)
and [transaction date contract](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/Data/IPPTransaction.php).

## Existing invoice and transport contract

Use `ConnectorAuth("oauth2_token", {"access_token": token}, config={"environment": "sandbox", "realm_id": realm_id})` and optional `CallOptions(http=caller_http, timeout=seconds, rate_limiter=caller_bucket)`. The caller owns an injected HTTP client; the connector closes only clients it creates. The numeric timeout overrides each HTTP request; a consumer that needs a whole-operation deadline must enforce it separately. Shared transport retries reads at most three times. Redirects are disabled even when a supplied client enables them. Numeric Retry-After is capped at 30 seconds per retry. OAuth renewal uses its separate explicit, non-retrying token transport.

Invoice queries use inclusive valid calendar dates on `TxnDate` and provider-order offset pagination. The query uses `STARTPOSITION` and `MAXRESULTS`, matching the [current official PHP SDK query builder](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/QueryFilter/QueryMessage.php). Explicit ordering is omitted because this increment does not require it and Invoice `Id` sort support was not established by primary evidence. Dates must be ordered, start position is 1-based (maximum 2147483647), and page size is 1–100. There is no arbitrary SQL, URL, write, or minor-version override. Sandbox requests use `sandbox-quickbooks.api.intuit.com`; production requests use `quickbooks.api.intuit.com`.

Every invoice carries `native_id`, exact UTF-8 `raw_json`, optional `source_revision` from supplied `SyncToken` and optional `source_updated_at` from supplied `MetaData.LastUpdatedTime`. Object text is sliced from the response, retaining whitespace, escaping, numeric precision and scale. Monetary numbers are validated with decimal decoding and are never decoded as binary floats or reserialized. Supplied totals/balances may be zero or negative. Missing currency and other optional fields remain missing; no currency is inferred. Amounts and balances describe the provider's current response, not historical balances or revenue.

The provider validates the returned envelope and metadata because the public output schema is descriptive. Duplicate JSON keys anywhere, duplicate invoice IDs in a page, invalid identities/amounts, unselectable transaction dates or dates outside the requested window, inconsistent positions/counts, invalid bounded source metadata and response bodies above 2 MiB fail safely. The response-size check occurs after shared HTTP buffering. Credential echoes in raw text or decoded keys/values fail before registry redaction; evidence is never silently scrubbed and returned as exact.

Returned temporal evidence is separate from strict request dates. Supplied
`MetaData.LastUpdatedTime` and guarded optional native dates such as Invoice/Bill
`DueDate` must be nonblank strings of at most 128 characters; they remain exact
untrusted text, with no parsing, trimming or reformatting. Missing optional values
stay absent. The consumer owns full temporal validity and any narrower custody
bounds; a successful read does not certify these strings as canonical values.

For transaction-window containment, a returned `TxnDate` must select a printed
four-digit `YYYY-MM-DD` from the finite Date/DateTime spelling: optional uppercase
`T` followed by `HH:MM:SS` and an optional decimal fraction, then optional `Z` or
`±HH:MM`. Compare that printed day inclusively with the strict request bounds.
Local times, offsets, fractions and hour 24 retain their supplied spelling; no UTC
conversion or next-day rollover is performed. Missing, nonstring, blank,
over-128-character, structurally unselectable or out-of-window transaction values
fail safely. Gregorian/time/offset validity and field precision remain consumer
responsibilities. Request dates still require valid unzoned calendar dates.
The 2 MiB complete-response ceiling remains independent of temporal validation.

A missing `Invoice` array in an otherwise empty `QueryResponse` means an empty page. Empty pages may omit position/count metadata; returned position then repeats the requested position and count is zero. Nonempty pages require `startPosition` and `maxResults` matching the request and returned count. Supplied `totalCount` is retained only as a nonnegative integer; its semantics are not used for termination.

The consumer owns page traversal, cross-page duplicate detection, aggregate size/count bounds, empty-scan handling, overflow detection, persistence and publication. A short page can terminate a declared traversal, but offset pagination is not a transactionally isolated snapshot. The returned `realm_id` repeats request context, not independent provider attestation. Provider faults return fixed safe error codes and status, never raw response bodies, tokens or transport exception text.

The explicit credential probe calls the existing CompanyInfo action and shares
its validation, transport, retry bounds and redaction. It reports only safe company
identity, keeps configured realm context distinct from `CompanyInfo.Id`, and does
not establish OAuth scope grants. See [authentication](auth.md) for its result.

Run local checks with `.venv/bin/python -B -m pytest tests/quickbooks_online tests/auth tests/catalog tests/packaging -p no:cacheprovider` and `.venv/bin/ruff check src/stackos_connectors/connectors/quickbooks_online tests/quickbooks_online`. The standalone installed-wheel driver in `tests/packaging/installed_auth_consumer.py` uses injected transports with live networking prohibited. Live Intuit consent/provider verification is a separate gate.

References: [Invoice API](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/invoice), [limits and throttling](https://static.developer.intuit.com/output_html/qbo/docs/learn/limits-and-throttles.html), [authentication](auth.md).
