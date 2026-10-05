# Google Ads protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/media-buying.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Search returns one page with an optional nextPageToken. Continue with the
same customer and query, passing that token as pageToken. Do not infer the
final page from the row count or automatically rewrite the query.

The reviewed connector interface uses page_cursor and returns
next_page_cursor; those names map to the native token fields above.
Preserve field masks, total counts and request IDs. An absent continuation
token marks the final provider page.

Service-account authentication still requires access for the account email,
a developer token and applicable manager/customer context. Acquiring a token
alone does not prove access to the selected Ads account.

Sources: [Search pagination](https://developers.google.com/google-ads/api/rest/common/search),
[REST authentication](https://developers.google.com/google-ads/api/rest/auth), and
[service accounts](https://developers.google.com/google-ads/api/docs/oauth/service-accounts).
