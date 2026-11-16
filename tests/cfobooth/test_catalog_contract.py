"""Wire invariants from CFO operation policy and canonical public error contracts."""

import copy
import json

import httpx
import pytest

from stackos_connectors import CallOptions
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.errors import ConnectorError, ValidationError

from .conftest import auth, descriptor


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(effect="mutation", replay="explicit-only"),
        lambda d: d.update(scope="public"),
        lambda d: d.update(authentication={"kind": "public"}),
        lambda d: d["authentication"].update(organizationParameter=None),
        lambda d: d["authentication"].update(credentials=["api-key", "api-key"]),
        lambda d: d.update(transport="binary"),
        lambda d: d["route"].update(body="multipart"),
    ],
)
async def test_inconsistent_catalog_policy_rejects_atomically(native, catalog, excluded, change):
    # assertOperationPolicy applies to every descriptor before eligibility filtering.
    item = catalog["data"]["operations"][1]
    if excluded:
        item["execution"] = {"kind": "excluded", "reason": "Excluded by server policy"}
    change(item)
    with pytest.raises(ValidationError):
        catalog_actions(catalog)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=catalog))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await native.execute(
                "cfobooth", "cfobooth.catalog.export", {}, auth(), CallOptions(http=http)
            )
    assert failed.value.metadata_json["reason_code"] == "invalid_catalog"
    assert len(native.registry.actions) == 1


@pytest.mark.parametrize("excluded", [False, True])
def test_duplicate_route_with_distinct_identity_rejects(catalog, excluded):
    duplicate = copy.deepcopy(catalog["data"]["operations"][1])
    duplicate["operationId"] = "fixture.duplicateRoute"
    if excluded:
        duplicate["execution"] = {"kind": "excluded", "reason": "Excluded by server policy"}
    catalog["data"]["operations"].append(duplicate)
    with pytest.raises(ValidationError):
        catalog_actions(catalog)


def test_different_methods_on_one_route_remain_distinct(catalog):
    another = copy.deepcopy(catalog["data"]["operations"][1])
    another["operationId"] = "fixture.anotherMethod"
    another["route"]["method"] = "POST"
    catalog["data"]["operations"].append(another)
    assert len(catalog_actions(catalog)) == 3


async def test_invalid_get_mutation_cannot_register_or_dispatch(native):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("invalid descriptor dispatched"))
    ) as http:
        with pytest.raises(ValidationError):
            bound = native.register_actions(
                [declare_action("cfobooth.invalid", descriptor(method="GET", mutation=True))]
            )
            await bound.execute(
                "cfobooth",
                "cfobooth.invalid",
                {"path_params": {"recordId": "one"}},
                auth(),
                CallOptions(http=http),
            )
    assert len(native.registry.actions) == 1


@pytest.mark.parametrize("schema", [{"type": "array"}, {"type": "string"}, {}])
def test_unsupported_query_root_rejects_before_registration(schema):
    item = descriptor(method="GET")
    item["schemas"]["query"] = schema
    with pytest.raises(ValidationError):
        declare_action("cfobooth.invalidQuery", item)


@pytest.mark.parametrize("replay,retry_safe", [("safe", True), ("explicit-only", False)])
@pytest.mark.parametrize("failure", ["timeout", "status", "oversize", "json", "media", "result"])
async def test_get_error_metadata_respects_declared_replay(native, replay, retry_safe, failure):
    item = descriptor(method="GET")
    item["replay"] = replay
    if failure == "oversize":
        item["responseLimit"] = 8
    bound = native.register_actions([declare_action("cfobooth.read", item)])
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if failure == "status":
            return httpx.Response(503, json={})
        if failure == "json":
            return httpx.Response(
                200, content=b"invalid", headers={"Content-Type": "application/json"}
            )
        if failure == "media":
            return httpx.Response(200, json={}, headers={"Content-Type": "text/html"})
        return httpx.Response(200, json={"invalid": "response"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.read",
                {"path_params": {"recordId": "one"}},
                auth(),
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is retry_safe


@pytest.mark.parametrize(
    "status,code,public_message",
    [
        (500, "internal_error", "The request could not be completed."),
        (403, "forbidden", "Access is denied."),
        (503, "dependency_unavailable", "A required service is unavailable."),
    ],
)
async def test_provider_error_text_cannot_escape_canonical_public_message(
    native, status, code, public_message
):
    private = "private database detail: synthetic-payroll-canary"
    error = {"requestId": "safe-correlation", "error": {"code": code, "message": private}}
    bound = native.register_actions([declare_action("cfobooth.write", descriptor(mutation=True))])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=error))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "write"}},
                auth(),
                CallOptions(http=http),
            )
    assert failed.value.provider_error == {
        "requestId": "safe-correlation",
        "error": {"code": code, "message": public_message},
    }
    assert private not in json.dumps(failed.value.output_json) + str(failed.value)
    assert failed.value.metadata_json["retry_safe"] is False
