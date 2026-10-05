import json

import pytest

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import load_registry


def test_packaged_provider_uses_grouped_resource_path():
    client = ConnectorClient(registry=load_registry("connectors/serper/catalog.json"))
    assert client.describe("serper")["actions"][0]["key"] == "serper.search"


@pytest.mark.parametrize(
    "path",
    [
        "/connectors/serper/catalog.json",
        "../catalog.json",
        "connectors/../serper/catalog.json",
        "connectors//serper/catalog.json",
        "connectors/serper/./catalog.json",
        "connectors\\serper\\catalog.json",
        "C:/catalog.json",
        "connectors/%2e%2e/catalog.json",
        "catalog/index.json",
        "catalog/schema.json",
        "",
    ],
)
def test_nested_paths_reject_absolute_traversal_and_reserved_resources(path):
    with pytest.raises(ValueError, match="resource"):
        load_registry(path)


def test_duplicate_resource_names_reject_before_loading():
    with pytest.raises(ValueError, match="duplicate"):
        load_registry("connectors/serper/catalog.json", "connectors/serper/catalog.json")


def test_nested_index_rejects_duplicate_resource_names(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/index.json").write_text(
        json.dumps(
            {
                "schema_version": "stackos.connectors.index.v1",
                "connectors": ["connectors/serper/catalog.json"] * 2,
            }
        )
    )
    with pytest.raises(ValueError):
        load_registry(root=tmp_path)


def test_nested_missing_resource_is_explicit():
    with pytest.raises(ValueError, match="resource"):
        load_registry("connectors/missing/catalog.json")
