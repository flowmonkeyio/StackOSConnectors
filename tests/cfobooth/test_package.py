"""The same resource check runs from source and the privately built wheel."""

import json
from importlib.resources import files

import pytest

from stackos_connectors import get_default_client
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.connectors.trackbooth import declare_action as trackbooth_declare_action
from stackos_connectors.connectors.trackbooth.schema import operation_input_schema
from stackos_connectors.probe import project_probe_config

from .conftest import ORG


def test_packaged_resources_and_retained_trackbooth_imports():
    root = files("stackos_connectors")
    for name in ["catalog.json", "schemas/catalog-v1.json", "schemas/current-key-v1.json"]:
        assert json.loads(root.joinpath("connectors/cfobooth", name).read_text())
    assert (
        catalog_actions and declare_action and trackbooth_declare_action and operation_input_schema
    )
    client = get_default_client()
    assert (
        client.describe("cfobooth")["probe_implementation"]
        == "stackos_connectors.connectors.cfobooth.probe:probe"
    )
    assert client.describe("trackbooth")["actions"][0]["key"] == "trackbooth.catalog.list"


def test_account_setup_and_runtime_config_contract():
    client = get_default_client()
    (method,) = client.describe("cfobooth", "cfobooth.catalog.export")["actions"][0]["auth_methods"]
    assert method["key"] == "api_key"
    fields = {field["key"]: field for field in method["setup"]["fields"]}
    assert set(fields) == {"api_key", "api_base_url"}
    assert fields["api_key"]["required"] and fields["api_key"]["secret"]
    assert fields["api_key"]["type"] == "password"
    assert fields["api_base_url"]["required"] and not fields["api_base_url"]["secret"]
    assert "default" not in fields["api_base_url"]
    assert method["fields_schema"]["required"] == ["api_key"]
    config = method["config_schema"]
    assert config["required"] == ["api_base_url"]
    assert config["additionalProperties"] is False
    assert set(config["properties"]) == {"api_base_url", "organization_id"}
    assert config["properties"]["organization_id"] == {"type": "string", "format": "uuid"}


@pytest.mark.parametrize("obsolete_org", ["Formx", ORG])
def test_probe_projection_discards_legacy_scope(obsolete_org):
    assert project_probe_config(
        "cfobooth",
        {
            "api_base_url": "http://localhost:8123",
            "organization_id": obsolete_org,
            "account_ref": "host-only",
        },
    ) == {"api_base_url": "http://localhost:8123"}
