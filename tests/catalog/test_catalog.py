from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import stackos_connectors
from stackos_connectors import (
    ConnectorClient,
    ConnectorError,
    ConnectorRegistry,
    ConnectorResult,
    ValidationError,
)
from stackos_connectors.catalog import default_registry, load_registry, registry_from_documents


@pytest.fixture
def document():
    return {
        "schema_version": "stackos.connector.v1",
        "connector": "fixture",
        "name": "Fixture Service",
        "description": "A complete service description.",
        "setup": {"note": "Acquire credentials in the service console."},
        "implementation": "not_imported_fixture:Connector",
        "auth_methods": [
            {
                "key": "oauth2_authorization_code",
                "description": "An already acquired access token.",
                "fields_schema": {
                    "type": "object",
                    "required": ["access_token"],
                    "properties": {"access_token": {"type": "string"}},
                },
                "config_schema": {"type": "object"},
                "setup": {"fields": ["client_id", "client_secret"]},
            }
        ],
        "actions": [
            {
                "key": "fixture.lookup",
                "operation": "lookup",
                "description": "Find the explicitly selected object.",
                "guidance": "Pass the service's native object identifier.",
                "input_schema": {
                    "type": "object",
                    "required": ["object_id"],
                    "properties": {"object_id": {"type": "string"}},
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object"},
                "auth_methods": ["oauth2_authorization_code"],
                "config": {"path": "/objects/{object_id}"},
                "examples": [{"object_id": "native-123"}],
                "metadata": {"name": "Find Object", "documentation": "https://example.test/api"},
            }
        ],
    }


def test_metadata_and_schemas_share_registry_without_importing_provider(document):
    client = ConnectorClient(registry=registry_from_documents([document]))
    described = client.describe("fixture")
    assert described["name"] == "Fixture Service"
    assert described["description"] == document["description"]
    assert described["setup"] == document["setup"]
    assert described["auth_methods"][0]["setup"]["fields"] == ["client_id", "client_secret"]
    action = described["actions"][0]
    assert action["key"] == "fixture.lookup"
    assert action["name"] == "Find Object"
    assert action["guidance"] == document["actions"][0]["guidance"]
    assert action["examples"] == [{"object_id": "native-123"}]
    assert action["auth_methods"][0]["fields_schema"]["required"] == ["access_token"]
    assert not client.validate_data("fixture", "fixture.lookup", {"object_id": "native-123"})
    assert client.validate_data("fixture", "fixture.lookup", {"object_ref": "saved-host-ref"})


def test_metadata_cannot_override_routing_or_schemas_and_is_detached(document):
    document["metadata"] = {"connector": "wrong", "available": False, "actions": ["wrong"]}
    document["actions"][0]["metadata"].update(
        {"key": "wrong", "input_schema": {}, "config": {"path": "/wrong"}}
    )
    client = ConnectorClient(registry=registry_from_documents([document]))
    document["setup"]["note"] = "changed"
    document["actions"][0]["input_schema"]["required"].clear()
    described = client.describe("fixture")
    assert described["connector"] == "fixture"
    assert described["available"] is True
    assert described["actions"][0]["key"] == "fixture.lookup"
    assert described["actions"][0]["input_schema"]["required"] == ["object_id"]
    assert described["actions"][0]["config"] == {"path": "/objects/{object_id}"}
    assert described["setup"]["note"] != "changed"
    described["setup"]["note"] = "mutated result"
    assert client.describe("fixture")["setup"]["note"] != "mutated result"
    scoped = client.register_actions([])
    assert scoped.describe("fixture") == client.describe("fixture")


def test_actionless_deferred_metadata_is_discoverable_but_not_executable():
    client = ConnectorClient(
        registry=registry_from_documents(
            [
                {
                    "schema_version": "stackos.connector.v1",
                    "connector": "deferred",
                    "name": "Deferred Service",
                    "description": "Planning metadata only.",
                    "execution_mode": "deferred",
                    "deferred_reason": "No implementation exists.",
                    "actions": [],
                    "auth_methods": [],
                }
            ]
        )
    )
    assert client.list_connectors() == ["deferred"]
    assert client.describe("deferred")["available"] is False
    assert client.describe("deferred")["actions"] == []
    with pytest.raises(ValidationError):
        client.validate_data("deferred", "invented", {})


def test_missing_binding_fails_honestly_on_dispatch(document):
    client = ConnectorClient(registry=registry_from_documents([document]))
    with pytest.raises(ConnectorError) as error:
        client.registry.implementation("fixture")
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["actions"][0].update(auth_methods=["unknown"]),
        lambda d: d["actions"].append(copy.deepcopy(d["actions"][0])),
        lambda d: d["auth_methods"].append(copy.deepcopy(d["auth_methods"][0])),
        lambda d: d["actions"][0].update(input_schema={"type": "invalid"}),
        lambda d: d.update(implementation="missing-colon"),
    ],
)
def test_invalid_catalog_rejected_before_provider_loading(document, change):
    change(document)
    with pytest.raises(ValueError):
        registry_from_documents([document])


def test_duplicate_connector_rejected(document):
    with pytest.raises(ValueError, match="duplicate"):
        registry_from_documents([document, copy.deepcopy(document)])


def test_resource_index_and_explicit_provider_loading(tmp_path: Path, document):
    (tmp_path / "fixture.json").write_text(json.dumps(document))
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "schema_version": "stackos.connectors.index.v1",
                "connectors": ["fixture.json"],
            }
        )
    )
    indexed = ConnectorClient(registry=load_registry(root=tmp_path))
    explicit = ConnectorClient(registry=load_registry("fixture.json", root=tmp_path))
    assert indexed.describe("fixture") == explicit.describe("fixture")


@pytest.mark.parametrize("filename", ["../fixture.json", "/fixture.json", "a/b.json", "index.json"])
def test_resource_names_cannot_escape_catalog(tmp_path: Path, filename: str):
    with pytest.raises(ValueError):
        load_registry(filename, root=tmp_path)


def test_missing_catalog_resource_is_not_silently_skipped(tmp_path: Path):
    with pytest.raises(ValueError, match="resource"):
        load_registry("missing.json", root=tmp_path)


def test_default_catalog_is_empty_and_public_functions_use_catalog(monkeypatch, document):
    assert ConnectorClient(registry=default_registry()).list_connectors() == []
    registry = registry_from_documents([document])
    monkeypatch.setattr("stackos_connectors.catalog.default_registry", lambda: registry)
    stackos_connectors.get_default_client.cache_clear()
    try:
        assert stackos_connectors.list_connectors() == ["fixture"]
        assert stackos_connectors.describe("fixture")["name"] == "Fixture Service"
    finally:
        stackos_connectors.get_default_client.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "optional,auth,allowed",
    [
        (False, None, False),
        (True, None, True),
        (True, {"method": "oauth2_authorization_code", "fields": {"access_token": "token"}}, True),
        (True, {"method": "wrong", "fields": {"access_token": "token"}}, False),
        (True, {"method": "oauth2_authorization_code", "fields": {}}, False),
    ],
)
async def test_optional_auth_preserves_anonymous_and_rejects_invalid_supplied_auth(
    document,
    optional,
    auth,
    allowed,
):
    document["actions"][0]["auth_optional"] = optional
    catalog = registry_from_documents([document])

    class Fixture:
        key = "fixture"
        calls = 0

        def validate(self, request):
            return []

        async def execute(self, request):
            self.calls += 1
            return ConnectorResult(output_json={"ok": True})

    fixture = Fixture()
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=catalog.actions.values(),
            implementations={"fixture": lambda: fixture},
            connector_metadata=catalog.connector_metadata,
        )
    )
    assert client.describe("fixture")["actions"][0]["auth_optional"] is optional
    if allowed:
        await client.execute("fixture", "fixture.lookup", {"object_id": "native"}, auth)
        assert fixture.calls == 1
    else:
        with pytest.raises(ValidationError):
            await client.execute("fixture", "fixture.lookup", {"object_id": "native"}, auth)
        assert fixture.calls == 0
