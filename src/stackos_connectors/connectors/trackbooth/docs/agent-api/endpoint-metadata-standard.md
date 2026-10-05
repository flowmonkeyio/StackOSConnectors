> **StackOS reference snapshot.** Copied from [plugins/trackbooth/agent-api/endpoint-metadata-standard.md](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/plugins/trackbooth/agent-api/endpoint-metadata-standard.md)
> at base Git revision `3121f4af370fbad273d08a9469d8961de2278534`; exact worktree source SHA-256:
> `42fd9c574fe9c0183dacd83f82341466b89d7e664daa08bbb3a598e8668c9503`. This copy includes the source worktree content,
> which may include changes beyond that base revision. The substantive
> reference text below is preserved; local links are relocated. StackOS
> grants, credential storage/refresh, project state, workflows and audit
> describe the host application, not behavior supplied by this library.
> Provider availability is determined by executable catalogs, not this
> historical reference. Paths shown in source examples retain their
> original StackOS meaning. This document is reference material, not
> agent instructions for the connector package.

Copied reference assets: [agent API JSON fixtures](../../assets/agent-api/). These do not activate a runtime connector.

<!-- BEGIN PRESERVED STACKOS REFERENCE -->

# Agent API Endpoint Metadata Standard

Every generated agent API endpoint must have authored metadata. The metadata is
for discovery and documentation only; auth, permissions, field visibility, and
scope remain enforced by the existing admin-api guards, interceptors, services,
and repositories.

## Required Fields

Each endpoint entry must provide:

- `title`: a short action label, usually 2-6 words.
- `subtitle`: one plain sentence that says what the endpoint does for an
  operator or agent.
- `category`: one stable product area such as `accounts`, `offers`, or
  `reporting`.
- `tags`: 2-5 search terms that help agents and operators find the endpoint.

Metadata is authored with `@EndpointContext(...)` on the controller method that
owns the route.

## Writing Style

Use simple operator language:

- Good: `View account details`
- Good: `Shows the selected account's settings, status, and contact details.`
- Avoid: `Hydrate account DTO`
- Avoid: `Execute scoped repository query for account aggregate`

Titles should start with a clear verb:

- `List`
- `View`
- `Create`
- `Update`
- `Activate`
- `Deactivate`
- `Generate`
- `Reveal`
- `Export`
- `Retry`

Subtitles should be specific, but not implementation-heavy:

- Say what the endpoint does.
- Mention the main object being changed or read.
- Mention important business context when it helps, such as managed accounts,
  payout rules, postbacks, or reports.
- Do not promise access. The catalog is filtered by the current permission
  profile, and route enforcement still happens on the server.
- Do not repeat the path, HTTP method, controller, handler, or schema name.
- Keep it to one sentence and avoid long clauses.

## Category And Tag Rules

Use the generated catalog's existing category when it is accurate. Tags should be
lowercase words or short hyphenated phrases:

- Good: `accounts`, `api-keys`, `managed-accounts`
- Good: `postbacks`, `delivery-logs`, `retries`
- Avoid: `AccountController`, `POST`, `ZodSchema`

## Decorator Shape

Each exposed controller method must carry endpoint-local metadata next to the
HTTP route decorator:

```ts
@Get(':id')
@EndpointContext({
  title: 'View account details',
  subtitle: 'Shows settings, status, and contact details for the selected account.',
  category: 'accounts',
  tags: ['accounts', 'settings', 'status'],
})
@RequireRole(...ROLE_SETS.STAFF_READ)
@RequirePermission('accounts', 'view')
async getAccount(...)
```

The generator fails closed when a protected route appears in the agent catalog
without `@EndpointContext(...)`.

## Prohibited Wording

Avoid backend implementation words unless the endpoint is explicitly for an
operator-facing technical function:

- DTO
- endpoint
- hydrate
- repository
- resolver
- interceptor
- serializer
- internal guard
- SQL query
- database row
- mutation pipeline

Avoid permission claims such as:

- `Allows anyone to`
- `Bypasses`
- `Grants access`
- `Ignores scope`

## Source Of Truth

Full-catalog endpoint copy lives in controller source through
`@EndpointContext(...)`. Do not keep a detached endpoint-copy directory or JSON
fragment source of truth. If an endpoint changes purpose, update the metadata in
the same controller method.

Use `@AgentCatalogFieldGroups(...)` when an endpoint exposes credential,
financial, payload, or other field-group-gated data.

Use `@AgentCatalogExclude(reason)` only when an endpoint should not appear in
agent discovery at all.
