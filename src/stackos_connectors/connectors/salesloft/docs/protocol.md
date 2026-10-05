# Salesloft protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Cadence enrollment creates a cadence membership with person_id and
cadence_id; user_id behavior depends on cadence ownership and team access.
It is distinct from creating a person.

Pagination uses page and per_page with endpoint-specific bounds. Rate
limiting is team-level and cost-based; deeper pages can cost more. Preserve
rate/cost context and distinguish invalid-sort 422 errors from 429 throttling.

Sources: [cadence membership](https://developers.salesloft.com/docs/api/cadence-memberships-create/),
[filtering and pagination](https://developers.salesloft.com/docs/platform/api-basics/filtering-paging-sorting/),
and [rate limits](https://developers.salesloft.com/docs/platform/api-basics/rate-limits/).
