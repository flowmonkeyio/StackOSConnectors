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

Version 0.1.0 has not yet been published to PyPI. After the first successful
publication, install the pinned release with:

```bash
python -m pip install stackos-connectors==0.1.0
```

Or add `stackos-connectors==0.1.0` to your Python dependency requirements.

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
