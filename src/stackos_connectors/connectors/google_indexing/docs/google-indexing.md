# Google Indexing API protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/google-indexing.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The Indexing API supports JobPosting pages and livestream BroadcastEvent
embedded in VideoObject, not ordinary articles. Removal notifications require
the page to return 404/410 or contain noindex. A service account requires
delegated site-owner access; FullUser alone is insufficient. The request scope
is `https://www.googleapis.com/auth/indexing`.

Publish sends `{url, type}` to
`POST https://indexing.googleapis.com/v3/urlNotifications:publish`;
type is URL_UPDATED or URL_DELETED. Receipt proves notification acceptance,
not indexing or removal. Metadata reads use
`GET https://indexing.googleapis.com/v3/urlNotifications/metadata?url=...`;
returned history, including empty history, is not current indexing status.

The batch endpoint is `https://indexing.googleapis.com/batch`.
A batch contains at most 100 inner calls; the reviewed contract bounds each
encoded inner request to 1,000,000 bytes. Quota is charged per inner call.
Execution order is unspecified and the batch is not transactional.

Correlate each response to its input, retaining known success/error receipts.
Missing, duplicate or malformed requested parts remain unknown. An outer HTTP
200 does not make failed inner requests successful. Preserve partial results
and Retry-After advice without repeating successful notifications.

A publish timeout, server error or malformed success receipt can leave an
unknown outcome. Repeating the entire batch can repeat successful parts.
Quota allowances depend on the Google project's approved quota.

Sources: [prerequisites](https://developers.google.com/search/apis/indexing-api/v3/prereqs),
[eligibility, removal and batching](https://developers.google.com/search/apis/indexing-api/v3/using-api),
[publish](https://developers.google.com/search/apis/indexing-api/v3/reference/indexing/rest/v3/urlNotifications/publish),
[metadata](https://developers.google.com/search/apis/indexing-api/v3/reference/indexing/rest/v3/urlNotifications/getMetadata),
and [quota](https://developers.google.com/search/apis/indexing-api/v3/quota-pricing).
