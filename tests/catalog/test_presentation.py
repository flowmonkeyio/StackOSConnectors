import copy
import json

import pytest

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import load_registry, registry_from_documents


@pytest.fixture
def presentation_document():
    return {
        "schema_version": "stackos.connector.v1",
        "connector": "fixture",
        "name": "Fixture",
        "description": "Read a selected provider object.",
        "icon": {
            "path": "connectors/fixture/assets/icon.svg",
            "media_type": "image/svg+xml",
            "kind": "wordmark-inverse",
        },
        "auth_methods": [
            {
                "key": "token",
                "description": "Resolved bearer token.",
                "fields_schema": {"type": "object"},
                "setup": {"description": "Acquire the token in the provider console."},
            }
        ],
        "actions": [
            {
                "key": "fixture.read",
                "operation": "read",
                "description": "Read one object.",
                "input_schema": {"type": "object"},
                "auth_methods": ["token"],
                "config": {},
                "metadata": {"name": "Read Object"},
            }
        ],
    }


def test_action_inherits_icon_and_auth_setup_without_mutating_metadata(presentation_document):
    document = presentation_document
    client = ConnectorClient(registry=registry_from_documents([document]))
    described = client.describe("fixture", "fixture.read")
    action = described["actions"][0]
    assert action["icon"] == described["icon"] == document["icon"]
    assert action["auth_methods"][0]["setup"] == document["auth_methods"][0]["setup"]
    action["icon"]["kind"] = "icon"
    action["auth_methods"][0]["setup"]["description"] = "changed"
    assert client.describe("fixture")["actions"][0]["icon"]["kind"] == "wordmark-inverse"
    fresh = client.describe("fixture")["actions"][0]
    assert fresh["auth_methods"][0]["setup"]["description"] != "changed"
    assert client.register_actions([]).describe("fixture") == client.describe("fixture")


@pytest.mark.parametrize(
    "kind", ["icon", "wordmark", "wordmark-dark", "wordmark-inverse", "fallback"]
)
def test_action_specific_icon_overrides_provider_and_preserves_kind(presentation_document, kind):
    explicit = {"path": "shared/icons/integration.svg", "media_type": "image/svg+xml", "kind": kind}
    presentation_document["actions"][0]["icon"] = explicit
    client = ConnectorClient(registry=registry_from_documents([presentation_document]))
    described = client.describe("fixture")
    assert described["actions"][0]["icon"] == explicit
    assert described["icon"] == presentation_document["icon"]


@pytest.mark.parametrize(
    "path",
    [
        "/icon.svg",
        "../icon.svg",
        "connectors/../icon.svg",
        "./icon.svg",
        "connectors//icon.svg",
        "connectors\\icon.svg",
        "https://example.com/icon.svg",
        "connectors/%2e%2e/icon.svg",
        "connectors/icon.svg?query",
        "connectors/icon.svg#part",
        "connectors/icon.svg\n",
    ],
)
@pytest.mark.parametrize("location", ["provider", "action_metadata"])
def test_invalid_icon_paths_rejected_before_provider_import(presentation_document, path, location):
    icon = copy.deepcopy(presentation_document["icon"])
    icon["path"] = path
    if location == "provider":
        presentation_document["icon"] = icon
    else:
        presentation_document["actions"][0]["metadata"]["icon"] = icon
    with pytest.raises(ValueError, match="catalog"):
        registry_from_documents([presentation_document])


@pytest.mark.parametrize(
    "icon",
    [
        {"path": "a.svg", "media_type": "image/png", "kind": "icon"},
        {"path": "a.png", "media_type": "image/jpeg", "kind": "icon"},
        {"path": "a.jpeg", "media_type": "image/webp", "kind": "icon"},
        {"path": "a.webp", "media_type": "image/svg+xml", "kind": "icon"},
        {"path": "a.svg", "media_type": "image/svg+xml", "kind": "logo"},
        {"path": "a.svg", "kind": "icon"},
        {"path": "a.svg", "media_type": "image/svg+xml", "kind": "icon", "url": "elsewhere"},
    ],
)
def test_invalid_icon_types_are_rejected(presentation_document, icon):
    presentation_document["icon"] = icon
    with pytest.raises(ValueError, match="catalog"):
        registry_from_documents([presentation_document])


def test_resource_loader_requires_icon_file(presentation_document, tmp_path):
    catalog = tmp_path / "fixture.json"
    catalog.write_text(json.dumps(presentation_document))
    with pytest.raises(ValueError, match="missing connector icon resource"):
        load_registry("fixture.json", root=tmp_path)
    icon = tmp_path / presentation_document["icon"]["path"]
    icon.parent.mkdir(parents=True)
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    client = ConnectorClient(registry=load_registry("fixture.json", root=tmp_path))
    assert client.describe("fixture")["actions"][0]["icon"] == presentation_document["icon"]


def test_search_console_auth_excludes_host_defaults_and_keeps_native_site_inputs():
    client = ConnectorClient(
        registry=load_registry("connectors/google_search_console/catalog.json")
    )
    described = client.describe("google-search-console")
    for method in described["auth_methods"]:
        config = method["config_schema"]
        assert not {"access_mode", "default_site_url"} & config.get("properties", {}).keys()
        assert not {"access_mode", "default_site_url"} & set(config.get("required", []))
        assert method["fields_schema"]["required"] == ["access_token"]
    selected = [
        a for a in described["actions"] if "site_url" in a["input_schema"].get("properties", {})
    ]
    assert selected
    assert all("site_url" in a["input_schema"]["required"] for a in selected)
