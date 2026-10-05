# StackOS Connectors

A Python library of named provider connectors. A caller supplies a connector,
action, input data and resolved authentication. The library maps that action to
the provider protocol and returns provider output, metadata and file descriptors.

The consumer owns credential storage and refresh, authorization, persistent state,
workflow decisions and audit. Provider implementations use plain contracts and
can accept caller-owned HTTP clients, rate buckets and native protocol sessions.

Each integration is grouped under `src/stackos_connectors/connectors/`:

```text
connectors/<provider>/
  actions.py       # named action mapping
  integration.py   # provider protocol client, when separate
  catalog.json     # descriptions, schemas, auth methods and fixed bindings
  schemas/         # provider-specific schema resources, when needed
  assets/          # fixed GraphQL documents and other provider resources
  docs/            # provider reference Markdown
shared/            # common transport, payload and protocol helpers
shared/google/     # Google OAuth and batch protocol helpers
catalog/           # common catalog schema and default resource index
references/        # shared StackOS reference snapshots and source provenance
```

Folder names use Python identifiers, such as `aws_s3`; public connector keys
remain unchanged, such as `aws-s3`. Provider-only helper modules live beside
their actions. The root package contains the public contracts and execution API.

This extraction is being assembled. Load the catalog resources you need
explicitly; the default index is currently empty. A documentation or asset
directory alone does not imply that an executable connector is installed.

```python
from stackos_connectors import ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry

client = ConnectorClient(registry=load_registry("connectors/serper/catalog.json"))
result = await client.execute(
    connector="serper",
    action="serper.search",
    data={"query": "example search"},
    auth=ConnectorAuth(method="api_key", fields={"api_key": resolved_api_key}),
)
```

`describe` supplies action descriptions, native input/output schemas and execution
auth requirements. `validate_data` checks the declared input without resolving
credentials. `validate`, `estimate_cost` and `execute` also check the selected auth
method. Dynamic actions must be explicitly registered on a scoped client before
execution; existing names cannot be replaced.

`CallOptions.timeout=None` preserves each provider's default. A numeric timeout
overrides that default. A supplied HTTP client remains caller-owned.

Copied Markdown is reference material from StackOS, with exact source hashes in
[the source map](src/stackos_connectors/references/source-map.json). Its grants,
credential lifecycle, project state, workflows and audit requirements describe
StackOS host behavior. Each copy identifies its source revision and links to
other copied references or explicit StackOS source locations. Provider URLs and
the substantive source text are retained. The library itself receives resolved
auth values and performs the selected provider request.

Python 3.12 or newer is required.
