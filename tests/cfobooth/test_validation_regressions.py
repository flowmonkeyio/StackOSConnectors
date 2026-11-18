"""Native boundary regressions for canonical destination, prefix and identity validation."""

import json

import httpx
import pytest

from stackos_connectors import CallOptions
from stackos_connectors.connectors.cfobooth import catalog_actions
from stackos_connectors.errors import IntegrationDownError, ValidationError
from stackos_connectors.probe import probe_credentials

from .conftest import KEY, ORG, auth


async def invoke(native, catalog, entry, base, http):
    credential, options = auth(base), CallOptions(http=http)
    if entry == "probe":
        return await probe_credentials("cfobooth", auth=credential, options=options)
    if entry == "catalog":
        return await native.execute("cfobooth", "cfobooth.catalog.export", {}, credential, options)
    bound = native.register_actions(catalog_actions(catalog))
    return await bound.execute("cfobooth", "cfobooth.fixture.read", {}, credential, options)


@pytest.mark.parametrize("entry", ["probe", "catalog", "operation"])
@pytest.mark.parametrize(
    "base", ["http://cfobooth\u3002com", "http://WWW.CFOBOOTH\uff0eCOM./tenant"]
)
async def test_normalized_remote_http_rejects_before_dispatch(native, catalog, entry, base):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("credential dispatched"))
    ) as http:
        with pytest.raises(ValidationError):
            await invoke(native, catalog, entry, base, http)


async def test_loopback_preserves_parameterized_prefix(native, peer):
    prefix = peer.base + ";partition=review"
    credential = auth(prefix)
    await probe_credentials("cfobooth", auth=credential)
    exported = await native.execute("cfobooth", "cfobooth.catalog.export", {}, credential)
    bound = native.register_actions(catalog_actions(exported.output_json))
    await bound.execute("cfobooth", "cfobooth.fixture.read", {}, credential)
    assert [call[1] for call in peer.calls] == [
        "/prefix;partition=review/api/api-keys/current",
        f"/prefix;partition=review/api/organizations/{ORG}/operation-catalog",
        f"/prefix;partition=review/api/organizations/{ORG}",
    ]
    assert all(call[2]["X-API-Key"] == KEY for call in peer.calls)


@pytest.mark.parametrize(
    "email",
    [
        "@",
        "no-local@",
        "@example.test",
        "line\nfeed@example.test",
        "double..dot@example.test",
        "trailing.@example.test",
        "a@localhost",
    ],
)
async def test_malformed_probe_email_fails_without_identity_or_secret(identity, email):
    identity["data"]["user"]["email"] = email
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=identity))
    ) as http:
        with pytest.raises(IntegrationDownError) as failed:
            await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))
    assert KEY not in json.dumps(failed.value.data) + str(failed.value)
    assert "identity" not in failed.value.data


@pytest.mark.parametrize("email", ["reader@example.test", "Reader.Name+scope@Example.Test"])
async def test_valid_probe_email_retains_verified_identity(identity, email):
    identity["data"]["user"]["email"] = email
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=identity))
    ) as http:
        result = await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))
    assert result["identity"]["user"] == identity["data"]["user"]
    assert result["identity"]["keyId"] == identity["data"]["keyId"]
