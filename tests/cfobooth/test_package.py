"""The same resource check runs from source and the privately built wheel."""

import json
from importlib.resources import files

from stackos_connectors import get_default_client
from stackos_connectors.connectors.cfobooth import catalog_actions, declare_action
from stackos_connectors.connectors.trackbooth import declare_action as trackbooth_declare_action
from stackos_connectors.connectors.trackbooth.schema import operation_input_schema
from stackos_connectors.probe import project_probe_config


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
    assert project_probe_config(
        "cfobooth",
        {
            "api_base_url": "http://localhost:8123",
            "organization_id": "org",
            "account_ref": "host-only",
        },
    ) == {"api_base_url": "http://localhost:8123", "organization_id": "org"}
