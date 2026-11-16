"""Independent schema and effect expectations, reusing the shared S1 owner."""

import copy
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft7Validator

from stackos_connectors import CallOptions
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ConnectorError, ValidationError
from stackos_connectors.shared.schema import normalize_draft7_schema

from .conftest import ORG, auth, descriptor


def s1_descriptor():
    item = descriptor()
    item["schemas"]["body"] = json.loads(
        (Path(__file__).parents[1] / "core/fixtures/structural-operation.json").read_text()
    )
    return item


VALID_BODY = {
    "body": {"mode": "preview", "details": {"label": "budget", "amount": 2.5, "tags": ["one"]}}
}


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra=True),
        lambda d: d.pop("body"),
        lambda d: d["body"].update(mode="wrong"),
        lambda d: d["body"]["details"].update(extra=True),
        lambda d: d["body"]["details"].update(amount=10),
        lambda d: d["body"]["details"].update(label="x"),
        lambda d: d["body"]["details"].update(tags=[]),
    ],
)
def test_complete_s1_constraints_at_native_admission(native, change):
    action = declare_action("cfobooth.s1", s1_descriptor())
    bound = native.register_actions([action])
    good = {"path_params": {"recordId": "one"}, "body": copy.deepcopy(VALID_BODY)}
    assert not bound.validate_data("cfobooth", "cfobooth.s1", good)
    bad = copy.deepcopy(good)
    change(bad["body"])
    assert bound.validate_data("cfobooth", "cfobooth.s1", bad)
    assert bound.validate_data("cfobooth", "cfobooth.s1", {"path_params": {"recordId": "one"}})
    assert "organizationId" not in action.input_schema["properties"]["path_params"]["properties"]
    assert thaw(action.input_schema)["properties"]["body"]["properties"]["body"][
        "x-server-refinements"
    ] == {"owner": "server", "rules": ["current-account-budget"]}


def test_effects_and_immutable_new_routes(native, catalog):
    actions = catalog_actions(catalog)
    assert actions[1].metadata["read_only"] is True  # POST remains a read.
    assert actions[1].metadata["replay"] == "safe"
    item = descriptor(mutation=True)
    action = declare_action("cfobooth.new", item)
    assert action.metadata["read_only"] is False
    assert action.metadata["effect"] == "mutation"
    assert action.metadata["replay"] == "explicit-only"
    item["route"]["path"] = "/wrong"
    assert action.config["path"] == "/api/organizations/:organizationId/future/:recordId"
    with pytest.raises(TypeError):
        action.config["descriptor"]["route"]["path"] = "/wrong"
    bound = native.register_actions([action])
    with pytest.raises(ValueError):
        bound.register_actions([action])
    assert len(native.registry.actions) == 1


def test_draft7_lossless_subset_and_reference_scope():
    source = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"$ref": "#/definitions/a%2562"}},
        "definitions": {"a%62": {"type": "string", "const": "right"}, "ab": {"const": "wrong"}},
        "examples": [{"$ref": "opaque"}],
    }
    projected = normalize_draft7_schema(source)
    from jsonschema import Draft202012Validator

    for value in [{"value": "right"}, {"value": "wrong"}, {"value": "right", "extra": 1}]:
        assert Draft7Validator(source).is_valid(value) == Draft202012Validator(projected).is_valid(
            value
        )
    assert projected["examples"] == [{"$ref": "opaque"}]
    assert source["$schema"].endswith("draft-07/schema#")


@pytest.mark.parametrize(
    "keyword",
    [
        {"items": [{"type": "string"}]},
        {"dependencies": {"a": ["b"]}},
        {"$ref": "#", "maxProperties": 2},
        {"prefixItems": []},
        {"$id": "https://other.test"},
    ],
)
def test_unsupported_draft7_features_explicitly_fail(keyword):
    with pytest.raises(ValidationError):
        normalize_draft7_schema({"$schema": "http://json-schema.org/draft-07/schema#", **keyword})


@pytest.mark.parametrize(
    "query", [{"value": None}, {"value": {}}, {"value": ["yes", None]}, {"value": [{}]}]
)
async def test_query_encoding_rejects_nonscalar_values(native, query):
    item = descriptor(method="GET")
    item["schemas"]["query"] = {"type": "object", "additionalProperties": True}
    bound = native.register_actions([declare_action("cfobooth.query", item)])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "cfobooth",
                "cfobooth.query",
                {"path_params": {"recordId": "one"}, "query": query},
                auth(),
                CallOptions(http=http),
            )


async def test_path_query_body_encoding_and_caller_http_lifetime(native):
    from urllib.parse import parse_qs

    item = descriptor()
    item["schemas"]["query"] = {"type": "object", "additionalProperties": True}
    bound = native.register_actions([declare_action("cfobooth.query", item)])
    calls = []

    def handle(request):
        calls.append(request)
        assert (
            request.url.raw_path.split(b"?")[0]
            == f"/prefix/api/organizations/{ORG}/future/one%2Ftwo%20space".encode()
        )
        assert parse_qs(request.url.query.decode()) == {
            "tag": ["one", "two"],
            "bool": ["true"],
            "n": ["2.5"],
        }
        assert json.loads(request.content) == {"name": "query"}
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(200, json={"requestId": "mapped", "data": {"status": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=91) as http:
        await bound.execute(
            "cfobooth",
            "cfobooth.query",
            {
                "path_params": {"recordId": "one/two space"},
                "query": {"tag": ["one", "two"], "bool": True, "n": 2.5},
                "body": {"name": "query"},
            },
            auth(),
            CallOptions(http=http, timeout=3),
        )
        assert not http.is_closed and http.timeout.read == 91
    assert len(calls) == 1


async def test_conflicting_body_organization_rejects(native):
    item = descriptor()
    item["schemas"]["body"] = {"type": "object", "additionalProperties": True}
    bound = native.register_actions([declare_action("cfobooth.conflict", item)])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "cfobooth",
                "cfobooth.conflict",
                {"path_params": {"recordId": "one"}, "body": {"organizationId": "foreign"}},
                auth(),
                CallOptions(http=http),
            )


@pytest.mark.parametrize(
    "status,body", [(302, {}), (204, {}), (200, {"data": {}}), (200, "not-json")]
)
async def test_invalid_operation_response_is_failure_without_replay(native, status, body):
    item = descriptor(mutation=True)
    bound = native.register_actions([declare_action("cfobooth.write", item)])
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://other.test"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "x"}},
                auth(),
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["retry_safe"] is False
    assert failed.value.metadata_json["outcome_unknown"] is True


def test_root_query_constraints_require_query_envelope(native):
    item = descriptor(method="GET")
    item["schemas"]["query"] = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "minProperties": 1,
        "additionalProperties": False,
    }
    bound = native.register_actions([declare_action("cfobooth.query", item)])
    assert bound.validate_data("cfobooth", "cfobooth.query", {"path_params": {"recordId": "one"}})
    assert not bound.validate_data(
        "cfobooth", "cfobooth.query", {"path_params": {"recordId": "one"}, "query": {"name": "yes"}}
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["schemas"]["params"].update(minProperties=1),
        lambda d: d["route"].update(path="/api/organizations/:organizationId/future/:missing"),
        lambda d: d.update(effect="mutation", replay="safe"),
        lambda d: d["route"].update(body="none"),
    ],
)
def test_unsupported_binding_shape_rejects_explicitly(change):
    item = descriptor()
    change(item)
    with pytest.raises(ValidationError):
        declare_action("cfobooth.invalid", item)


async def test_oversized_write_response_closes_and_stays_uncertain(native):
    from .conftest import KEY

    item = descriptor(mutation=True)
    item["responseLimit"] = 64
    bound = native.register_actions([declare_action("cfobooth.write", item)])
    response = httpx.Response(200, content=(KEY * 20).encode())
    calls = []

    def handle(request):
        calls.append(request)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "x"}},
                auth(),
                CallOptions(http=http),
            )
    assert response.is_closed and len(calls) == 1
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert KEY not in str(failed.value) + str(failed.value.output_json)


async def test_permissive_error_schema_cannot_release_unbounded_raw_error(native):
    item = descriptor(mutation=True)
    item["schemas"]["error"] = {"type": "object"}
    bound = native.register_actions([declare_action("cfobooth.write", item)])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"unsafe": "x" * 10000}))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "x"}},
                auth(),
                CallOptions(http=http),
            )
    assert failed.value.provider_error is None


@pytest.mark.parametrize("content,media", [(b"not JSON", "application/json"), (b"{}", "text/html")])
async def test_non_json_transport_responses_fail(native, content, media):
    bound = native.register_actions([declare_action("cfobooth.read", descriptor(method="GET"))])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=content, headers={"Content-Type": media})
        )
    ) as http:
        with pytest.raises(ConnectorError):
            await bound.execute(
                "cfobooth",
                "cfobooth.read",
                {"path_params": {"recordId": "one"}},
                auth(),
                CallOptions(http=http),
            )
