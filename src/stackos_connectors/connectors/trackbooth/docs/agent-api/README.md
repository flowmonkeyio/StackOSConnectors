# Trackbooth API resource snapshots

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/plugins/trackbooth/agent-api/README.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The retained resources have distinct client-facing roles:

- [catalog.json](../../assets/agent-api/catalog.json): endpoint catalog snapshot.
- [openapi.json](../../assets/agent-api/openapi.json): request/response schema snapshot.
- [stackos-tools.json](../../assets/agent-api/stackos-tools.json): compact REST
  operation metadata supplied with the bundle.

The authenticated live catalog and server responses determine account-visible
operations, permission requirements, feature access and on-behalf authority.
A bundled snapshot is neither an access grant nor a current availability check.
See the [client protocol notes](../trackbooth.md).
