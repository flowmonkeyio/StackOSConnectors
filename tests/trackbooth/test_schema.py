"""Canonical constraints survive projection, declarations and native validation."""

from copy import deepcopy

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.trackbooth import declare_action
from stackos_connectors.connectors.trackbooth.assets import _expand_schema_descriptor
from stackos_connectors.connectors.trackbooth.schema import _schema_property
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ValidationError


def canonical_schema():
    return {
        "type": "object",
        "properties": {
            "assignments": {"type": "array", "items": {"type": "string"}},
            "version": {"type": "integer", "minimum": 0},
        },
        "dependentRequired": {"assignments": ["version"]},
        "additionalProperties": False,
        "x-trackbooth-constraints": {"opaque": ["provider-owned"]},
        "description": "Canonical update",
        "$comment": "Preserve all root keywords",
    }


@pytest.mark.parametrize("closed", [True, False])
def test_expanded_schema_is_preserved_and_deeply_independent(closed):
    raw = canonical_schema()
    if not closed:
        raw.pop("additionalProperties")
    original = deepcopy(raw)
    projected = _schema_property(raw)
    assert projected == original
    projected["properties"]["assignments"]["items"]["type"] = "number"
    projected["dependentRequired"]["assignments"].append("other")
    projected["x-trackbooth-constraints"]["opaque"].append("changed")
    assert raw == original


def test_canonical_and_component_fallback_keep_asset_owner():
    canonical = canonical_schema()
    component = {"type": "object", "properties": {"legacy": {"type": "boolean"}}}
    descriptor = {
        "json_schema": canonical,
        "component_name": "Legacy",
        "details": {"fields": [{"name": "invented", "type": "string"}]},
    }
    assert (
        _schema_property(_expand_schema_descriptor(descriptor, {"Legacy": component})) == canonical
    )
    descriptor.pop("json_schema")
    assert (
        _schema_property(_expand_schema_descriptor(descriptor, {"Legacy": component})) == component
    )
    assert _schema_property(_expand_schema_descriptor(descriptor, {})) is None


def declaration():
    return declare_action(
        "trackbooth.fixture.update",
        {
            "operation_id": "Fixture.update",
            "method": "PATCH",
            "path": "/api/fixtures/one",
            "body_schema": {"json_schema": canonical_schema()},
        },
    )


def test_native_declaration_preserves_canonical_constraints():
    assert thaw(declaration().input_schema)["properties"]["body"] == canonical_schema()


@pytest.mark.parametrize(
    "body,valid",
    [
        ({"assignments": []}, False),
        ({"assignments": ["one"]}, False),
        ({"assignments": [], "version": 0}, True),
        ({"assignments": ["one"], "version": 0}, True),
        ({}, True),
        ({"extra": True}, False),
    ],
)
async def test_native_dependency_validation_stops_before_transport(body, valid):
    bound = ConnectorClient(registry=load_registry("connectors/trackbooth/catalog.json"))
    bound = bound.register_actions([declaration()])
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        args = (
            "trackbooth",
            "trackbooth.fixture.update",
            {"body": body},
            ConnectorAuth(
                "api-key", {"api_key": "synthetic"}, {"api_base_url": "https://example.test"}
            ),
            CallOptions(http=http),
        )
        if valid:
            await bound.execute(*args)
        else:
            with pytest.raises(ValidationError):
                await bound.execute(*args)
    assert len(calls) == int(valid)
