# StackOS Connectors

A Python library of named provider connectors. A caller supplies a connector,
action, input data and resolved authentication. The library maps that action to
the provider protocol and returns provider output, metadata and file descriptors.

The consumer owns credential storage, when to authorize or refresh, persistent
state, workflow decisions and audit. Explicit package functions implement provider
authorization URLs, token grants, probes and signature checks. They never start
authorization, refresh tokens or persist state implicitly. Provider implementations use plain contracts and
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
```

Folder names use Python identifiers, such as `aws_s3`; public connector keys
remain unchanged, such as `aws-s3`. Provider-only helper modules live beside
their actions. The root package contains the public contracts and execution API.

The default client loads the bundled connector catalogs. Use `list_connectors`
and `describe` to discover the installed actions and their input/auth schemas.
Generic HTTP accepts explicitly registered declarations; OpenRouter currently
provides a credential probe without a text-generation action.

```python
from stackos_connectors import ConnectorAuth, get_default_client

client = get_default_client()
result = await client.execute(
    connector="serper",
    action="serper.search",
    data={"query": "example search"},
    auth=ConnectorAuth(method="api_key", fields={"api_key": resolved_api_key}),
)
```

`describe` supplies provider and action names, descriptions, native input/output
schemas, setup information and execution auth requirements. Auth method `setup`
describes credential acquisition; `fields_schema` and `config_schema` define the
resolved auth accepted for execution. `validate_data` checks input without resolving
credentials. `validate`, `estimate_cost` and `execute` also check the selected auth
method. Dynamic actions must be explicitly registered on a scoped client before
execution; existing names cannot be replaced.

`CallOptions.timeout=None` preserves each provider's default. A numeric timeout
overrides that default. A supplied HTTP client remains caller-owned.

QuickBooks Online provides bounded company/invoice reads with exact invoice JSON
fragments and explicit OAuth. See the [provider contract](src/stackos_connectors/connectors/quickbooks_online/docs/quickbooks-online.md).

## Explicit authentication

`get_auth_contract(connector, method=..., config=...)` reads immutable provider
facts from the bundled catalog. `build_authorization_request` formats a consent
URL using the caller's redirect URI, state and optional PKCE challenge. The caller
opens that URL, handles the callback and checks its state. `request_token` performs
one requested `authorization_code`, `refresh_token`, `client_credentials` or
`jwt_bearer` grant, where the selected method declares support. Meta's declared
second token exchange is part of that explicit request.

```python
from stackos_connectors import ConnectorAuth, request_token

token = await request_token(
    "google-search-console",
    auth=ConnectorAuth(
        method="oauth2_authorization_code",
        fields={"client_id": client_id, "client_secret": client_secret},
    ),
    grant_type="authorization_code",
    code=validated_callback_code,
    redirect_uri=registered_redirect_uri,
    code_verifier=stored_pkce_verifier,
    http=caller_http,
)
```

Grant calls accept resolved application/key fields; action and probe calls accept
the method's resolved execution fields, usually an access token. The catalog's
`setup` and `protocol` describe acquisition separately from `fields_schema` and
`config_schema`. Manual token methods retain their declared refresh compatibility;
the library does not infer an auth method from token shape.

`TokenResult` holds sensitive in-process values. An absent `refresh_token` means
the response did not rotate it. `scopes_present=False` distinguishes omitted
scope evidence from an explicitly empty grant. For a successful Google JWT grant,
the immutable contract supplies the signed request's scopes; the consumer can
apply the reviewed omission rule without changing the token response's provenance.
The caller decides how to preserve prior evidence, enforce permissions and store
results. `OAuthTokenError` exposes bounded categories/status and repair facts,
without raw provider response bodies. Repr suppression is not a storage policy.

`probe_credentials(connector, auth=..., options=..., context=...)` explicitly
tests a resolved credential through a fixed provider binding. It returns safe
provider facts and optional `AuthProbeEvidence`; it does not establish application
readiness or permissions. `project_probe_config` projects portable config and
retains supported legacy probe aliases. The caller chooses the IMAP native mailbox
through `CallOptions(provider_context={"mailbox": ...})`. Google Ads/Workspace
service-account probes only report resolved-token presence and unverified resource
access; they make no HTTP call or new-grant claim.

Google JWT validation/signing is shared across the five declared service-account
methods. Credentials never come from ambient identity or key-supplied endpoints.
Provider details live in `connectors/<provider>/docs/auth.md` and the catalog.
Google Indexing retains its existing resolved-token action/probe contract; this
release adds no Indexing JWT acquisition method.

Provider-local pure helpers cover Telegram authorization request/challenge/state
translation (`connectors.telegram.auth`), Slack v0 HMAC verification
(`connectors.slack_bot.auth.verify_signature_v0`) and HubSpot v3 HMAC verification
(`connectors.hubspot.signature.verify_signature_v3`). Callers own native sessions,
encrypted custody, challenge expiry, ingress routing, trusted canonical URI,
timestamp/replay checks and policy. HubSpot v2 verification is not implemented.

No library call to discovery, probe or action execution starts an authorization
flow, refreshes a token, opens a native session, stores credentials or selects
readiness. Remote token revocation is not part of this API.

For file-producing actions, pass `CallOptions(output_dir=Path(...))` and consume
the plain descriptors in `result.files`. The library does not create application
artifacts or public download URLs. Telegram actions require an already authorized
session through `CallOptions(native_session=session)`; the caller owns account
lifecycle, the TDLib binary, receipt persistence and session shutdown.

For a restricted catalog, construct `ConnectorClient(registry=load_registry(...))`
using `stackos_connectors.catalog.load_registry` and the provider-local resource
paths above. Register custom HTTP or Trackbooth declarations on that scoped client
before execution; request data cannot override a declaration's provider or route.

`ConnectorResult.output_json` contains native response data for in-process use,
including signed download URLs and pagination tokens. Exact resolved credential
values are scrubbed from that data. The consumer must project or redact it before
display, audit or persistent storage. Errors, progress, metadata and file
descriptors receive full key and text redaction. Results hide response data in
their default representation.

Provider protocol notes and source links live in each connector's `docs/`
directory. They cover authentication transport, requests, pagination, response
meaning and error handling. Use the executable catalog and `describe` for the
installed action contract.

Existing integration icons are bundled at `connectors/<provider>/assets/icon.*`.
Each catalog's `icon` object contains a package-relative `path`, `media_type` and
`kind`. Actions inherit the integration icon unless an action declares its own.
Kinds preserve the source presentation: `icon`, `wordmark`, `wordmark-dark` or
`wordmark-inverse`. Missing logos use the neutral `fallback` at
`shared/icons/integration.svg`. Standard package resources read the bytes without
a source checkout:

```python
from importlib.resources import files

description = client.describe("serper", "serper.search")
icon = description["actions"][0]["icon"]
icon_bytes = files("stackos_connectors").joinpath(*icon["path"].split("/")).read_bytes()
```

## Installation

Python 3.12 or newer is required. Source:
[flowmonkeyio/StackOSConnectors](https://github.com/flowmonkeyio/StackOSConnectors).

Published releases can be installed with:

```bash
python -m pip install stackos-connectors==0.2.2
```

Or add `stackos-connectors==0.2.2` to your Python dependency requirements.

Install from a local checkout with `pip install /path/to/StackOSConnectors`, or
build a wheel with `python -m build --wheel` and install that wheel. Runtime use
needs only the installed package and its declared dependencies.

## Publishing

The manual [publish workflow](.github/workflows/publish.yml) uses PyPI Trusted
Publishing through GitHub OIDC. It does not store a PyPI API token. Pushing a
commit does not publish a release.

Before running it, configure the GitHub `pypi` environment and a PyPI GitHub
trusted publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `stackos-connectors` |
| GitHub owner | `flowmonkeyio` |
| Repository | `StackOSConnectors` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

Use a pending publisher for the first release. See the official
[PyPI trusted publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

After configuration and release review, open **Actions → Publish to PyPI → Run
workflow** and select `main`. The build job checks out the selected commit,
builds the wheel and source distribution on Python 3.12, checks their metadata,
and uploads them as one workflow artifact. The separate publish job downloads
that artifact and requests the OIDC token inside the `pypi` environment.
Runs selected on other branches do not build or publish. Only the publish job
has `id-token: write` permission.

The workflow does not rerun the connector test suite; run the tests and review
the intended release commit before dispatch. A version already uploaded to
PyPI cannot be replaced; change the package version for a later release.
