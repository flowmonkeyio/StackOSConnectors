"""Wire invariants from CFO operation policy and canonical public error contracts."""

import copy
import json
import logging
import traceback

import httpx
import pytest

from stackos_connectors import CallOptions
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.errors import ConnectorError, ValidationError

from .conftest import KEY, ORG, auth, descriptor, wire


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


GROUP_COUNT = {"reason": "presentation_group_count", "path": "definition.operation.groupBy"}
PERIOD_SELECTION = {
    "reason": "financial_period_selection",
    "path": "definition.operation.filter.equals",
}
PERIOD_WINDOW = {"reason": "financial_period_window", "path": "definition.operation.range"}
PRIVATE_DETAIL = "synthetic-payroll-canary"
PRIVATE_TOKEN = "syntheticModelCanary"
PUBLIC_ERROR = {
    "requestId": "query-400.fixture",
    "error": {"code": "invalid_request", "message": "The request is invalid."},
}


@pytest.fixture
def financial_catalog(catalog):
    # The shared v1 agreement supplies the issue grammar. The pinned producer
    # baseline supplies the operation identity, method, route and body binding.
    # Keep the synthetic request/result schemas here small and structural.
    item = catalog["data"]["operations"][2]
    item["operationId"] = "datasets.executeQuery"
    item["route"]["path"] = (
        "/api/organizations/:organizationId/data-sources/:sourceId/datasets/:datasetId/queries"
    )
    schemas = item["schemas"]
    params = schemas["params"]
    for name in ("sourceId", "datasetId"):
        params["required"].append(name)
        params["properties"][name] = params["properties"]["organizationId"]
    schemas["body"].update(
        required=["applicationId", "definition"],
        properties={
            "applicationId": params["properties"]["organizationId"],
            "definition": {"type": "object", "required": ["selection", "operation"]},
        },
    )
    schemas["error"]["properties"]["error"]["properties"]["issues"] = wire(
        "financial-query-issues-v1"
    )
    return catalog, item


@pytest.fixture
def execute_financial_query(native, financial_catalog, caplog):
    caplog.set_level(logging.INFO)

    async def execute(status, reply, operation=None):
        catalog, item = financial_catalog
        operation_id = item["operationId"]
        client = native.register_actions(catalog_actions(catalog))
        source = "53761da2-37bc-40ab-afbb-489fb72a67a1"
        dataset = "576bb550-1dcc-430b-bbf2-c3327f603027"
        body = {
            "applicationId": "53761da2-37bc-40ab-afbb-489fb72a67a2",
            "definition": {
                "selection": {"kind": "fixed"},
                "operation": operation or {"kind": "rows", "query": {"limit": 1, "offset": 0}},
            },
        }
        calls = []

        def respond(request):
            calls.append(request)
            assert request.method == "POST"
            assert str(request.url) == (
                f"https://owned.test/prefix/api/organizations/{ORG}"
                f"/data-sources/{source}/datasets/{dataset}/queries"
            )
            assert request.headers["x-api-key"] == KEY
            assert "Authorization" not in request.headers
            assert json.loads(request.content) == body
            return httpx.Response(status, json=reply)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            try:
                return await client.execute(
                    "cfobooth",
                    f"cfobooth.{operation_id}",
                    {"path_params": {"sourceId": source, "datasetId": dataset}, "body": body},
                    auth(),
                    CallOptions(http=http),
                )
            except ConnectorError as exc:
                return exc
            finally:
                assert len(calls) == 1

    return execute


def assert_public_query_error(failed, status, expected, caplog):
    assert isinstance(failed, ConnectorError)
    assert failed.provider_status_code == status
    assert failed.provider_error == expected
    output = {"status": "failed", "provider_status_code": status}
    if expected is not None:
        output["provider_error"] = expected
    assert failed.output_json == output
    assert failed.metadata_json == {
        "vendor": "cfobooth",
        "status_code": status,
        "provider_executed": True,
        "retry_safe": False,
        "outcome_unknown": status >= 500,
    }
    published = (
        json.dumps(failed.output_json)
        + json.dumps(failed.metadata_json)
        + "".join(traceback.format_exception(failed))
        + caplog.text
    )
    assert PRIVATE_DETAIL not in published
    assert PRIVATE_TOKEN not in published
    assert KEY not in published


def query_error(issues, *, code="invalid_request", request_id="query-400.fixture"):
    return {
        "requestId": request_id,
        "error": {"code": code, "message": f"{PRIVATE_DETAIL}: {KEY}", "issues": issues},
    }


@pytest.mark.parametrize(
    "operation,issues",
    [
        pytest.param(
            {"kind": "aggregate", "groupBy": ["scope_key", "scenario_key"], "sumField": "amount"},
            [GROUP_COUNT],
            id="group-count",
        ),
        pytest.param(
            {
                "kind": "aggregate",
                "filter": {"equals": [{"field": "period_kind", "value": "flow"}]},
            },
            [{**PERIOD_SELECTION, "field": "measure_key"}],
            id="missing-measure",
        ),
        pytest.param(
            {
                "kind": "aggregate",
                "filter": {"equals": [{"field": "measure_key", "value": "sales"}]},
            },
            [{**PERIOD_SELECTION, "field": "period_kind"}],
            id="missing-period-kind",
        ),
        pytest.param(
            {
                "kind": "trend",
                "interval": "month",
                "range": {
                    "field": "period_start",
                    "basis": "supplied_calendar",
                    "from": "2024-11-01",
                    "to": "2025-02-28",
                },
            },
            [PERIOD_WINDOW],
            id="spanning-window",
        ),
        pytest.param(None, [PERIOD_SELECTION], id="server-omits-field"),
        pytest.param(
            None,
            [
                {**PERIOD_WINDOW, "path": "definition.operation.filter.dateRange"},
                {**PERIOD_WINDOW, "path": "definition.operation.current"},
                {**PERIOD_WINDOW, "path": "definition.operation.baseline"},
                {**PERIOD_SELECTION, "field": "basis"},
            ],
            id="four-issues-in-server-order",
        ),
    ],
)
async def test_financial_issue_survives_native_error(
    execute_financial_query, caplog, operation, issues
):
    failed = await execute_financial_query(400, query_error(issues), operation)
    expected = {**PUBLIC_ERROR, "error": {**PUBLIC_ERROR["error"], "issues": issues}}
    assert_public_query_error(failed, 400, expected, caplog)


async def test_financial_issue_accepts_closed_any_of_branches(
    execute_financial_query, financial_catalog, caplog
):
    _, item = financial_catalog
    items = item["schemas"]["error"]["properties"]["error"]["properties"]["issues"]["items"]
    items["anyOf"] = items.pop("oneOf")
    issues = [{**PERIOD_SELECTION, "field": "scenario_key"}]
    failed = await execute_financial_query(400, query_error(issues))
    expected = {**PUBLIC_ERROR, "error": {**PUBLIC_ERROR["error"], "issues": issues}}
    assert_public_query_error(failed, 400, expected, caplog)


@pytest.mark.parametrize(
    "issues",
    [
        pytest.param([], id="empty"),
        pytest.param([GROUP_COUNT] * 5, id="five-issues"),
        pytest.param(None, id="null"),
        pytest.param(GROUP_COUNT, id="non-array"),
        pytest.param([KEY], id="non-object"),
        pytest.param([{**GROUP_COUNT, "reason": PRIVATE_DETAIL}], id="unknown-reason"),
        pytest.param([{**GROUP_COUNT, "path": PERIOD_WINDOW["path"]}], id="mismatched-pair"),
        pytest.param([{**GROUP_COUNT, "field": "measure_key"}], id="field-on-grouping"),
        pytest.param([{**PERIOD_SELECTION, "field": PRIVATE_DETAIL}], id="unknown-field"),
        pytest.param([{**PERIOD_SELECTION, "field": {"value": KEY}}], id="nested-field"),
        pytest.param([{**GROUP_COUNT, "path": "x" * 4000}], id="excessive-string"),
        pytest.param([{**GROUP_COUNT, "value": f"https://owned.test/{KEY}"}], id="extra-value"),
        pytest.param([GROUP_COUNT, {"reason": PRIVATE_DETAIL}], id="reject-whole-list"),
    ],
)
async def test_invalid_financial_issue_falls_back(execute_financial_query, caplog, issues):
    failed = await execute_financial_query(400, query_error(issues))
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


@pytest.mark.parametrize(
    "status,code,public_message",
    [
        (500, "invalid_request", "The request is invalid."),
        (400, "forbidden", "Access is denied."),
    ],
)
async def test_financial_issue_on_wrong_status_or_code_falls_back(
    execute_financial_query, caplog, status, code, public_message
):
    failed = await execute_financial_query(status, query_error([GROUP_COUNT], code=code))
    assert_public_query_error(
        failed,
        status,
        {**PUBLIC_ERROR, "error": {"code": code, "message": public_message}},
        caplog,
    )


@pytest.mark.parametrize("request_id", [f"https://owned.test/{KEY}", "x" * 129])
async def test_financial_issue_with_unsafe_correlation_is_detail_free(
    execute_financial_query, caplog, request_id
):
    failed = await execute_financial_query(400, query_error([GROUP_COUNT], request_id=request_id))
    assert_public_query_error(failed, 400, None, caplog)


async def test_financial_issue_with_unsafe_base_error_is_detail_free(
    execute_financial_query, caplog
):
    reply = query_error([GROUP_COUNT])
    reply["error"]["message"] *= 20
    failed = await execute_financial_query(400, reply)
    assert_public_query_error(failed, 400, None, caplog)


@pytest.mark.parametrize("legacy_descriptor", [False, True])
async def test_legacy_generic_query_error(
    execute_financial_query, financial_catalog, caplog, legacy_descriptor
):
    if legacy_descriptor:
        _, item = financial_catalog
        del item["schemas"]["error"]["properties"]["error"]["properties"]["issues"]
    error = query_error([GROUP_COUNT])
    if not legacy_descriptor:
        del error["error"]["issues"]
    failed = await execute_financial_query(400, error)
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


async def test_financial_issue_on_another_operation_falls_back(
    execute_financial_query, financial_catalog, caplog
):
    _, item = financial_catalog
    item["operationId"] = "datasets.loadQuery"
    failed = await execute_financial_query(400, query_error([GROUP_COUNT]))
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


@pytest.mark.parametrize(
    "weakness", ["open-envelope", "open-issue", "free-text", "unpaired", "unbounded"]
)
async def test_weak_financial_issue_descriptor_falls_back(
    execute_financial_query, financial_catalog, caplog, weakness
):
    _, item = financial_catalog
    error_schema = item["schemas"]["error"]
    issues = error_schema["properties"]["error"]["properties"]["issues"]
    branches = issues["items"]["oneOf"]
    if weakness == "open-envelope":
        error_schema["additionalProperties"] = True
    elif weakness == "open-issue":
        branches[0]["patternProperties"] = {".*": {}}
    elif weakness == "free-text":
        branches[1]["properties"]["field"] = {"type": "string", "maxLength": 64}
    elif weakness == "unpaired":
        branches[0]["properties"]["reason"] = {
            "enum": ["presentation_group_count", "financial_period_window"]
        }
    else:
        del issues["maxItems"]
    failed = await execute_financial_query(400, query_error([GROUP_COUNT]))
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


async def test_weak_positional_financial_issue_schema_falls_back(
    execute_financial_query, financial_catalog, caplog
):
    _, item = financial_catalog
    error_schema = item["schemas"]["error"]
    del error_schema["$schema"]  # Draft 2020-12 applies items only after prefixItems.
    issues = error_schema["properties"]["error"]["properties"]["issues"]
    issues["prefixItems"] = [{}]
    failed = await execute_financial_query(
        400, query_error([{**GROUP_COUNT, "path": PRIVATE_TOKEN}])
    )
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


async def test_recursive_financial_issue_schema_falls_back(
    execute_financial_query, financial_catalog, caplog
):
    _, item = financial_catalog
    error_schema = item["schemas"]["error"]
    del error_schema["$schema"]
    issues = error_schema["properties"]["error"]["properties"]["issues"]
    leaf = issues["items"]["oneOf"][0]["properties"]["reason"]
    leaf["$ref"] = "#/properties/error/properties/issues/items/oneOf/0/properties/reason"
    failed = await execute_financial_query(400, query_error([GROUP_COUNT]))
    assert_public_query_error(failed, 400, PUBLIC_ERROR, caplog)


async def test_valid_financial_query_keeps_native_result(execute_financial_query):
    reply = {"requestId": "query-success.fixture", "data": {"status": "ok"}}
    result = await execute_financial_query(200, reply)
    assert result.output_json == reply
    assert result.metadata_json == {
        "vendor": "cfobooth",
        "request_id": "query-success.fixture",
        "operation_id": "datasets.executeQuery",
        "provider_executed": True,
        "status_code": 200,
    }
