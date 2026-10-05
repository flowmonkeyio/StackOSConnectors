import json
import re
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import PurePosixPath

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import load_registry


def test_grouped_owners_have_no_old_duplicate_paths():
    root = resources.files("stackos_connectors")
    for old in ["actions", "integrations", "assets", "s3_contract.py"]:
        assert not root.joinpath(old).is_file()
        assert not root.joinpath(old).is_dir()
    providers = list(root.joinpath("connectors").iterdir())
    catalogs = [
        path.joinpath("catalog.json")
        for path in providers
        if path.is_dir() and path.joinpath("catalog.json").is_file()
    ]
    assert catalogs
    documents = [json.loads(path.read_text()) for path in catalogs]
    connector_keys = [doc["connector"] for doc in documents]
    action_keys = [action["key"] for doc in documents for action in doc["actions"]]
    assert len(connector_keys) == len(set(connector_keys))
    assert len(action_keys) == len(set(action_keys))
    assert {"serper", "shopify"} <= set(connector_keys)
    assert "serper.search" in action_keys
    assert all(
        "stackos_connectors.connectors." in doc["implementation"]
        for doc in documents
        if "implementation" in doc
    )
    for provider in providers:
        if not provider.is_dir() or not provider.joinpath("catalog.json").is_file():
            continue
        assert provider.name.isidentifier()
        document = json.loads(provider.joinpath("catalog.json").read_text())
        if "implementation" in document:
            assert document["implementation"].startswith(
                f"stackos_connectors.connectors.{provider.name}."
            )
        client = ConnectorClient(registry=load_registry(f"connectors/{provider.name}/catalog.json"))
        assert document["connector"] in client.list_connectors()
        described = client.describe(document["connector"])
        assert described["name"] and described["description"]
        assert described["icon"] == document["icon"]
        assert root.joinpath(*described["icon"]["path"].split("/")).read_bytes()
        for action, raw in zip(described["actions"], document["actions"], strict=True):
            assert action["name"] and action["description"]
            expected_icon = raw.get("icon", raw.get("metadata", {}).get("icon", document["icon"]))
            assert action["icon"] == expected_icon
            for method in action["auth_methods"]:
                assert method["description"] and method["setup"]["description"]
        for link in document.get("setup", {}).get("docs", []):
            if not link.startswith("https://"):
                assert root.joinpath(*link.split("/")).is_file(), link
    assert ConnectorClient(registry=load_registry()).list_connectors() == sorted(connector_keys)


def test_provider_documents_are_colocated_and_local_links_resolve():
    root = resources.files("stackos_connectors")
    assert not root.joinpath("references").is_dir()
    pending = [(root.joinpath("connectors"), PurePosixPath("connectors"))]
    documents = []
    while pending:
        directory, relative = pending.pop()
        for resource in directory.iterdir():
            path = relative / resource.name
            if resource.is_dir():
                pending.append((resource, path))
            elif resource.name.endswith(".md"):
                assert "docs" in path.parts
                assert resource.name != "AGENTS.md"
                documents.append((resource, path))
    assert documents
    for resource, path in documents:
        content = resource.read_text()
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", link) or link.startswith("#"):
                continue
            target = link.split("#", 1)[0]
            parts = list(path.parent.parts)
            for part in PurePosixPath(target).parts:
                if part == "..":
                    assert parts, (path, link)
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            resource = root.joinpath(*parts)
            assert resource.is_file() or resource.is_dir(), (path, link)


def test_companion_provider_resources_are_readable():
    root = resources.files("stackos_connectors").joinpath("connectors")
    for name in ["linear", "shopify", "trackbooth"]:
        pending = [root.joinpath(name, "assets")]
        schemas = root.joinpath(name, "schemas")
        if schemas.is_dir():
            pending.append(schemas)
        assert pending[0].is_dir(), name
        while pending:
            for resource in pending.pop().iterdir():
                if resource.is_dir():
                    pending.append(resource)
                elif resource.name.endswith(".json"):
                    assert isinstance(json.loads(resource.read_text()), dict)
                elif resource.name.endswith(".graphql"):
                    assert re.search(r"\b(query|mutation)\b", resource.read_text())


def test_icon_resources_are_readable_and_fallback_is_neutral():
    root = resources.files("stackos_connectors")
    icons = []
    for provider in root.joinpath("connectors").iterdir():
        assets = provider.joinpath("assets")
        if assets.is_dir():
            icons.extend(item for item in assets.iterdir() if item.name.startswith("icon."))
    assert icons
    for icon in icons:
        data = icon.read_bytes()
        if icon.name.endswith(".svg"):
            assert ET.fromstring(data).tag == "{http://www.w3.org/2000/svg}svg"
        elif icon.name.endswith(".png"):
            assert data.startswith(b"\x89PNG\r\n\x1a\n")
        elif icon.name.endswith((".jpg", ".jpeg")):
            assert data.startswith(b"\xff\xd8\xff")
        elif icon.name.endswith(".webp"):
            assert data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        else:
            raise AssertionError(f"Unsupported icon type: {icon.name}")
    fallback = ET.fromstring(root.joinpath("shared/icons/integration.svg").read_bytes())
    assert fallback.find("{http://www.w3.org/2000/svg}title").text == "Generic integration"
