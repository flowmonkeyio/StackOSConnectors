"""CFO native public caller controls against the reviewed wire agreement."""

import pytest

from stackos_connectors import CallOptions, ConnectorAuth, probe_credentials
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.errors import ConnectorError, ValidationError

from .conftest import KEY, ORG, auth, descriptor

OTHER_ORG = "73837d14-fbd6-40ca-81f4-19e2ce031fca"


@pytest.mark.parametrize("legacy_org", [None, "Formx", OTHER_ORG])
async def test_loopback_probe_catalog_register_execute_and_denial(native, peer, legacy_org):
    setup = auth(peer.base + "/", org=legacy_org)
    identity = await probe_credentials("cfobooth", auth=setup)
    assert identity["identity"]["organizationId"] == ORG
    assert identity["identity"]["keyId"] == "53761da2-37bc-40ab-afbb-489fb72a67a1"
    assert identity["identity"]["user"]["id"] == "576bb550-1dcc-430b-bbf2-c3327f603027"
    assert identity["api_base_url"] == peer.base
    evidence = identity["metadata"]["evidence"]["account"]
    assert evidence["provider_account_id"] == identity["identity"]["user"]["id"]
    assert evidence["metadata"] == {
        "organization_id": ORG,
        "key_id": identity["identity"]["keyId"],
        "expires_at": None,
    }
    assert [(method, path) for method, path, _, _ in peer.calls] == [
        ("GET", "/prefix/api/api-keys/current"),
    ]

    # Model the host injecting verified authority and filtering by the public schema.
    runtime_config = {
        **setup.config,
        "account_ref": "host-only",
        "organization_id": evidence["metadata"]["organization_id"],
    }
    (method,) = native.describe("cfobooth")["auth_methods"]
    properties = method["config_schema"]["properties"]
    credential = ConnectorAuth(
        setup.method,
        setup.fields,
        {key: value for key, value in runtime_config.items() if key in properties},
    )
    catalog = await native.execute("cfobooth", "cfobooth.catalog.export", {}, credential)
    actions = catalog_actions(catalog.output_json)
    assert {item.key for item in actions} == {"cfobooth.fixture.read", "cfobooth.fixture.query"}
    bound = native.register_actions(actions)
    assert not bound.validate_data(
        "cfobooth", "cfobooth.fixture.query", {"body": {"name": "preview"}}
    )
    read = await bound.execute("cfobooth", "cfobooth.fixture.read", {}, credential)
    assert read.output_json == {"requestId": "operation-c09", "data": {"status": "ok"}}
    with pytest.raises(ValidationError):
        await bound.execute(
            "cfobooth",
            "cfobooth.fixture.read",
            {"path_params": {"organizationId": OTHER_ORG}},
            credential,
        )
    result = await bound.execute(
        "cfobooth",
        "cfobooth.fixture.query",
        {"body": {"name": "preview"}},
        credential,
        CallOptions(timeout=2),
    )
    assert result.output_json == {"requestId": "operation-c09", "data": {"status": "ok"}}
    assert [call[0] for call in peer.calls] == ["GET", "GET", "GET", "POST"]
    assert [call[1] for call in peer.calls] == [
        "/prefix/api/api-keys/current",
        f"/prefix/api/organizations/{ORG}/operation-catalog",
        f"/prefix/api/organizations/{ORG}",
        f"/prefix/api/organizations/{ORG}/datasets/semantic/aggregate",
    ]
    assert all(call[2]["X-API-Key"] == KEY for call in peer.calls)
    assert all("Authorization" not in call[2] for call in peer.calls)
    assert peer.calls[-1][3] == b'{"name":"preview"}'
    peer.status = 403
    peer.output = {
        "requestId": "denied-c09",
        "error": {"code": "forbidden", "message": "Access is denied."},
    }
    with pytest.raises(ConnectorError) as failed:
        await bound.execute("cfobooth", "cfobooth.fixture.read", {}, credential)
    assert failed.value.provider_error == peer.output
    assert len(peer.calls) == 5  # Stored action does not re-fetch catalog or refresh.


async def test_loopback_synthetic_mutation_and_redirect(native, peer):
    bound = native.register_actions([declare_action("cfobooth.write", descriptor(mutation=True))])
    data = {"path_params": {"recordId": "one/two"}, "body": {"name": "write"}}
    await bound.execute("cfobooth", "cfobooth.write", data, auth(peer.base))
    assert peer.calls[0][1] == f"/prefix/api/organizations/{ORG}/future/one%2Ftwo"
    assert peer.calls[0][0] == "POST"
    peer.status = 302
    peer.headers = {"Location": "https://other.test"}
    with pytest.raises(ConnectorError) as failed:
        await bound.execute("cfobooth", "cfobooth.write", data, auth(peer.base))
    assert len(peer.calls) == 2
    assert failed.value.metadata_json["retry_safe"] is False
