"""Compose owned provider documents with the accepted package catalog loader."""

import json
from importlib import resources

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import registry_from_documents

PROVIDERS = [
    "ahrefs",
    "aws-s3",
    "cloudflare",
    "dataforseo",
    "firecrawl",
    "ftp",
    "ghost",
    "google-analytics",
    "google-indexing",
    "google-paa",
    "google-search-console",
    "google-tag-manager",
    "http",
    "jina",
    "openrouter",
    "reddit",
    "serper",
    "shopify",
    "sitemap",
    "wordpress",
]


def document(provider):
    return json.loads(
        resources.files("stackos_connectors")
        .joinpath("connectors", provider.replace("-", "_"), "catalog.json")
        .read_text()
    )


def client_for(provider, *, anonymous=False):
    del anonymous
    return ConnectorClient(registry=registry_from_documents([document(provider)]))
