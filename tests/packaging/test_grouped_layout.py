import hashlib
import json
import re
from importlib import resources
from pathlib import PurePosixPath

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import load_registry


def sha256(data):
    return hashlib.sha256(data).hexdigest()


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
    assert len(catalogs) == 20
    documents = [json.loads(path.read_text()) for path in catalogs]
    assert sum(len(doc["actions"]) for doc in documents) == 115
    assert all(
        "stackos_connectors.connectors." in doc["implementation"]
        for doc in documents
        if "implementation" in doc
    )
    assert ConnectorClient(registry=load_registry()).list_connectors() == []


def test_copied_references_preserve_source_body_and_provider_urls():
    root = resources.files("stackos_connectors")
    mapping = json.loads(root.joinpath("references/source-map.json").read_text())
    assert len(mapping["documents"]) == 29
    assert sum(item["target"].startswith("connectors/") for item in mapping["documents"]) == 16
    for item in mapping["documents"]:
        resource = root.joinpath(*item["target"].split("/"))
        content = resource.read_text()
        assert sha256(content.encode()) == item["copied_sha256"]
        assert PurePosixPath(item["target"]).name != "AGENTS.md"
        assert item["source_sha256"] in content
        assert mapping["source_base_revision"] in content
        body = content.split("<!-- BEGIN PRESERVED STACKOS REFERENCE -->\n\n", 1)[1]
        assert sha256(body.encode()) == item["relocated_body_sha256"]
        normalized = body
        for link in item["links"]:
            normalized = normalized.replace(f"]({link['target']})", "](<STACKOS_LINK>)")
        assert sha256(normalized.encode()) == item["substantive_body_sha256"]
        for url in item["source_external_urls"]:
            assert url in body


def test_reference_asset_hashes_and_local_document_links_resolve():
    root = resources.files("stackos_connectors")
    mapping = json.loads(root.joinpath("references/source-map.json").read_text())
    assert len(mapping["assets"]) == 42
    for item in mapping["assets"].values():
        assert sha256(root.joinpath(*item["target"].split("/")).read_bytes()) == item["sha256"]
    for item in mapping["documents"]:
        content = root.joinpath(*item["target"].split("/")).read_text()
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", link) or link.startswith("#"):
                continue
            target = link.split("#", 1)[0]
            parts = list(PurePosixPath(item["target"]).parent.parts)
            for part in PurePosixPath(target).parts:
                if part == "..":
                    assert parts, (item["target"], link)
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            resource = root.joinpath(*parts)
            assert resource.is_file() or resource.is_dir(), (item["target"], link)
