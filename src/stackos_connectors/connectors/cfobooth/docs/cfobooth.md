# CFOBooth API-key protocol

This provider implements the independently reviewed `sdk837-wire-r6` agreement
for plan837. The bundled `schemas/catalog-v1.json` and
`schemas/current-key-v1.json` are protocol validation resources from that
agreement, not a second business-operation registry. The catalog transport schema
uses an open, safe `/api` path-template shape rather than the sample's route enum.
The test fixture under `tests/cfobooth/fixtures` is the frozen illustrative catalog
and synthetic identity; it does not prove server implementation or key issuance.

Use native `ConnectorAuth(method="api_key", fields={"api_key": ...},
config={"api_base_url": ..., "organization_id": ...})`. The host's Account owns
secret custody and the selected organization. Configuration is required and
editable. `https://cfobooth.com` is display-only example text and is rejected as a
request destination. No remote deployment is verified. Remote endpoints require
HTTPS; HTTP is permitted only for `localhost`, `127.0.0.1` and `[::1]`, with an
optional port. The native Trackbooth URL policy is reused with CFO's explicit
configuration requirement. Check the final IDNA-normalized wire host before
dispatch and preserve the deployment path prefix, including semicolon parameters.
Normalize trailing slashes. Reject userinfo, query/fragment, escapes, backslashes, traversal and
invalid ports before dispatch. Provider context cannot override URL, headers or
organization. Caller-owned HTTP clients remain trusted transport dependencies;
the caller owns their middleware, authentication, proxy and TLS configuration.

`probe_credentials("cfobooth", ...)` performs one key-only
`GET /api/organizations/:organizationId/api-keys/current` with `X-API-Key`. It
validates the canonical envelope, UUID key/user/organization identity, bounded
email syntax from CFOBooth's shared VerifiedUser contract,
configured organization and nullable future UTC expiration. It returns safe
`identity` and native account evidence, including key ID and subject; no key or
digest is returned. A malformed, expired, revoked or foreign key fails safely.
The server remains authoritative for key validity and current permissions.
There is no login, password acquisition, token grant, refresh or remote logout.

Execute `cfobooth.catalog.export` to fetch
`GET /api/organizations/:organizationId/operation-catalog`. It preserves the
canonical `{requestId,data:{version,catalogHash,operations}}` envelope. Version1,
512-operation and16MiB ceilings, unique operation identities and method/path bindings, descriptor shapes
and eligible schema projection are checked before returning a complete catalog.
Scope, authentication, transport and effect consistency are validated for all
descriptors before eligibility filtering, including excluded operations.
`catalog_actions(result.output_json)` returns an immutable tuple of eligible
`ActionDefinition`s for explicit `client.register_actions`. An empty supported
catalog returns an empty tuple. It never registers a partial catalog itself.
Excluded, public, session-only and specialized descriptors do not become actions;
calling `declare_action` directly on one rejects it. Newly discovered operation
IDs and safe organization routes need no handwritten handler.

An action stores the declared route, status, schemas, effect, replay and key
eligibility metadata. Ordinary execution uses that stored binding without
fetching the catalog. The organization path comes only from Account config and
is removed from model inputs. Other path values are encoded as one segment;
missing, extra, conflicting or traversal parameters reject before dispatch.
Query scalars stringify, arrays repeat keys, absent values are omitted, and
null/object entries reject. JSON bodies retain the complete canonical schema.
Read-only POST remains read-only; a mutation requires explicit-only replay.
GET must declare a read effect. Retry metadata also respects explicit-only replay,
including for GET; a read-only POST does not acquire automatic replay permission.

Shared projection preserves supported strict objects, required bodies, unions,
bounds, nested constraints and local references. Draft7 opts into the shared
structural subset; tuple items, dependencies, reference siblings, unknown
features and external references fail explicitly. Annotation values remain
opaque. Fixed organization specialization supports a finite object parameter
schema with declared required path properties; other root parameter constraints
reject rather than disappear. Full original parameters are also validated after
organization injection. Structural validation does not execute server semantic
refinements or establish authorization.

Requests reuse `shared.http.request_json`: bounded decoded responses, response
closure, caller-owned clients/timeouts, redirects disabled and no retries.
Operation success must match its declared status and result schema. Failures
retain only structurally valid canonical error identity/correlation; unsafe raw
bodies are discarded. Canonical error codes and correlation IDs retain only the
fixed public message from CFOBooth's wire contract, never provider-supplied text.
Resolved credential echoes are scrubbed at the native
boundary. A write timeout or invalid response stays an uncertain outcome, with
no automatic replay. The operation response envelope is preserved.

The host owns durable inventory, full versus limited synchronization, stale
removal and scope identity (project, credential reference, normalized base URL,
verified subject, organization and key ID). Changing credentials or endpoint
requires host revalidation and inventory invalidation; this stateless library
cannot fence host caches or late results. Disconnect removes host custody only;
remote revocation is explicit CFOBooth key administration. Browser viewers retain
their own sessions and never inherit the integration key.

Local proof uses public native discovery/probe/validation/registration/execution
with owned loopback peers plus frozen-wire security/schema controls. Actual
issued keys, SQL/current authority, real emitted catalog parity and composed
StackOS traffic remain tickets17/16. Independent exact-commit review and operator
installation are separate gates. Version0.2.9 is a private candidate; no package
publication or installed StackOS change is implied.
