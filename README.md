# StackOS Connectors

A Python library of named provider connectors. A caller supplies a connector,
action, input data and resolved authentication. The library maps that action to
the provider protocol and returns provider output, metadata and file descriptors.

The consumer owns credential storage and refresh, authorization, persistent state,
workflow decisions and audit. Provider implementations use plain contracts and
can accept caller-owned HTTP clients, rate buckets and native protocol sessions.

This extraction is being assembled. The core supports explicitly registered
connectors; built-in provider composition follows the provider migration.

```python
from stackos_connectors import ConnectorAuth, ConnectorClient

# Construct the client with the catalog/registry for your installed connectors.
client = ConnectorClient(registry=registry)
result = await client.execute(
    connector="provider",
    action="provider.action",
    data={"message": "Hello"},
    auth=ConnectorAuth(method="api_key", fields={"token": resolved_token}),
)
```

`describe` supplies action descriptions, native input/output schemas and execution
auth requirements. `validate_data` checks the declared input without resolving
credentials. `validate`, `estimate_cost` and `execute` also check the selected auth
method. Dynamic actions must be explicitly registered on a scoped client before
execution; existing names cannot be replaced.

Python 3.12 or newer is required.
