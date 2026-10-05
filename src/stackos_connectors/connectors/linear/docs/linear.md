# Linear GraphQL protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/linear.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The endpoint is `POST https://api.linear.app/graphql`.
OAuth uses `Authorization: Bearer <access token>`; a personal API key is
the complete Authorization header value without a Bearer prefix. Select the
transport from the declared method, not from token appearance.

Fixed operation documents are in [assets/graphql](../assets/graphql/);
[pinned schema resources](../schemas/) provide the introspection snapshot,
its provenance and the root policy. Workflow-state queries represent Linear
issue states; they are provider resources.

Connection reads return pageInfo. Issue relation listing can traverse
issue.relations because the workspace-wide issueRelations root lacks an
issue filter. The reviewed schema did not deprecate issueSearch; omitting an
operation from a connector is not evidence of provider deprecation.

GraphQL errors can accompany HTTP 200 and partial data. Preserve that
distinction and mutation success:false. Linear's HTTP 400 with GraphQL
RATELIMITED is a rate-limit response distinct from HTTP 429. Preserve request,
rate-limit and query-complexity metadata when returned.

A write transport failure after dispatch can leave an unknown outcome.
Do not infer safe retry or successful mutation from a local request ID.

Sources: [GraphQL](https://linear.app/developers/graphql),
[authentication](https://linear.app/developers/sdk),
[OAuth](https://linear.app/developers/oauth-2-0-authentication),
[pagination](https://linear.app/developers/pagination),
[rate limits](https://linear.app/developers/rate-limiting), and
[public schema](https://studio.apollographql.com/public/Linear-API/schema/reference?variant=current).
