# Trackbooth Agent API protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/trackbooth.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The default API base is `https://apis.trackbooth.com`. Remote custom bases
use HTTPS; the reviewed client allows plain HTTP only for localhost,
127.0.0.1 or ::1 test targets. The base belongs to connection configuration,
not arbitrary per-operation payload.

X-API-Key authenticates account-key requests. The server also documents
Authorization: ApiKey and JWT Bearer forms. X-Acting-As-Account is a separate
execution-context header, not an endpoint body/query/path field. The server
allows self or actively managed accounts and applies the relationship's
permission profile. Account API keys require agent_api.access on the backing
account.

GET /api/agent-api/catalog and its /{operationId} detail return operations
visible to the authenticated permission context. The bulk export endpoint is
/api/agent-api/catalog/export. Provider feature gates, permissions and
on-behalf restrictions remain authoritative at request time.

A descriptor's object json_schema is authoritative. Legacy details.fields
is not a substitute for that schema; referenced OpenAPI components provide
a fallback only when the descriptor lacks json_schema. Preserve provider
permission and feature fields: they describe remote requirements, not
client-side workflow decisions.

[Bundled API resources](agent-api/README.md) are reference snapshots, not a
promise that every operation is available to a particular account.

Additional source: [provider REST contract supplied with the API bundle](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/plugins/trackbooth/agent-api/agent-api-rest-tools.md).
