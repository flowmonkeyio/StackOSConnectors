# Apollo protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

People API Search and enrichment have different outputs and costs. The
reviewed People Search contract requires a master API key and does not
return email addresses or phone numbers. Its page/per_page interface caps
displayed results at 50,000 (100 per page, up to 500 pages); use narrower
explicit filters instead of assuming every matching record is retrievable.

Bulk organization enrichment accepts up to ten companies per request and
consumes credits. Preserve endpoint-specific errors, page metadata and
credit information rather than treating search and enrichment as one action.

Sources: [People API Search](https://docs.apollo.io/reference/people-api-search),
[bulk organization enrichment](https://docs.apollo.io/reference/bulk-organization-enrichment),
and [API pricing](https://docs.apollo.io/docs/api-pricing).
