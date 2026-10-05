# Google Search Console protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/google-search-console.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

Search Console reads accept the webmasters.readonly scope or the broader
webmasters scope. Sitemap submission requires webmasters plus the property's
write permission. A token's scope alone does not prove property access.

Sitemap submission uses an empty-body PUT with encoded site and sitemap
URL path parameters. Site identifiers can be URL-prefix properties, with
their trailing slash, or `sc-domain:example.com`. Acceptance registers or
resubmits the URL; it does not edit the sitemap or guarantee crawling/indexing.

HTTP batches use `POST https://searchconsole.googleapis.com/batch`.
Supported inner requests include Webmasters paths and
`/v1/urlInspection/index:inspect`. The reviewed batch contract permits up
to 1,000 calls. Quota is charged per inner call, execution order is unspecified,
and there is no transaction or all-or-nothing guarantee.

Correlate response parts to input indices and preserve each known receipt.
Missing, duplicate or malformed requested parts remain unknown. Unexpected
parts and envelope defects must not erase valid receipts or count as requested
work. An HTTP 200 envelope can contain failures.

Retain safe provider status, errors and Retry-After advice. A failed response
after sitemap submission may leave an unknown outcome. Repeating a batch can
repeat successful writes; no stronger idempotency guarantee follows from the
batch transport.

Sources: [authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing),
[submission](https://developers.google.com/webmaster-tools/v1/sitemaps/submit),
[scope alternatives](https://developers.google.com/webmaster-tools/v1/sitemaps/list),
[batch contract](https://developers.google.com/webmaster-tools/v1/how-tos/batch),
[discovery document](https://searchconsole.googleapis.com/$discovery/rest?version=v1),
and [usage limits](https://developers.google.com/webmaster-tools/limits).
