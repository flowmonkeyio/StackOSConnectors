"""CFO native public caller controls against the reviewed wire agreement."""

from stackos_connectors import get_default_client


def test_default_discovery_includes_cfo_without_contact():
    description = get_default_client().describe("cfobooth")
    assert description["auth_methods"][0]["key"] == "api_key"
    assert description["auth_methods"][0]["config_schema"]["required"] == [
        "api_base_url",
        "organization_id",
    ]


async def test_loopback_probe_catalog_register_execute_and_denial(native, peer):
    from stackos_connectors import CallOptions
    from stackos_connectors.connectors.cfobooth import catalog_actions
    from stackos_connectors.errors import ConnectorError
    from stackos_connectors.probe import probe_credentials

    from .conftest import KEY, ORG, auth

    credential = auth(peer.base + "/")
    identity = await probe_credentials("cfobooth", auth=credential)
    assert identity["identity"]["organizationId"] == ORG
    assert identity["identity"]["keyId"] == "53761da2-37bc-40ab-afbb-489fb72a67a1"
    assert identity["identity"]["user"]["id"] == "576bb550-1dcc-430b-bbf2-c3327f603027"
    catalog = await native.execute("cfobooth", "cfobooth.catalog.export", {}, credential)
    actions = catalog_actions(catalog.output_json)
    assert {item.key for item in actions} == {"cfobooth.fixture.read", "cfobooth.fixture.query"}
    bound = native.register_actions(actions)
    assert not bound.validate_data(
        "cfobooth", "cfobooth.fixture.query", {"body": {"name": "preview"}}
    )
    result = await bound.execute(
        "cfobooth",
        "cfobooth.fixture.query",
        {"body": {"name": "preview"}},
        credential,
        CallOptions(timeout=2),
    )
    assert result.output_json == {"requestId": "operation-c09", "data": {"status": "ok"}}
    assert [call[0] for call in peer.calls] == ["GET", "GET", "POST"]
    assert [call[1] for call in peer.calls] == [
        f"/prefix/api/organizations/{ORG}/api-keys/current",
        f"/prefix/api/organizations/{ORG}/operation-catalog",
        f"/prefix/api/organizations/{ORG}/datasets/semantic/aggregate",
    ]
    assert all(call[2]["X-API-Key"] == KEY for call in peer.calls)
    assert all("Authorization" not in call[2] for call in peer.calls)
    assert peer.calls[-1][3] == b'{"name":"preview"}'
    peer.status = 403
    peer.output = {"requestId": "denied-c09", "error": {"code": "forbidden", "message": "revoked"}}
    import pytest

    with pytest.raises(ConnectorError) as failed:
        await bound.execute("cfobooth", "cfobooth.fixture.read", {}, credential)
    assert failed.value.provider_error == peer.output
    assert len(peer.calls) == 4  # Stored action does not re-fetch catalog or refresh.


async def test_loopback_synthetic_mutation_and_redirect(native, peer):
    import pytest

    from stackos_connectors.connectors.cfobooth import declare_action
    from stackos_connectors.errors import ConnectorError

    from .conftest import ORG, auth, descriptor

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
