from __future__ import annotations

import importlib
import json
import socket

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
from stackos_connectors.errors import IntegrationDownError, ValidationError
from stackos_connectors.probe import (
    AuthMethodProbeContext,
    probe_credentials,
    project_probe_config,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("live provider network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.mark.parametrize(
    "connector,config,expected",
    [
        (
            "wordpress",
            {"wp_url": "https://first.test", "site_url": "https://second.test"},
            {"wp_url": "https://first.test"},
        ),
        ("wordpress", {"site_url": "https://legacy.test"}, {"wp_url": "https://legacy.test"}),
        ("wordpress", {"base_url": "https://base.test"}, {"wp_url": "https://base.test"}),
        (
            "ghost",
            {"ghost_url": "https://first.test", "site_url": "https://second.test"},
            {"ghost_url": "https://first.test"},
        ),
        (
            "ghost",
            {"base_url": "https://base.test", "api_version": "v5.0"},
            {"ghost_url": "https://base.test", "api_version": "v5.0"},
        ),
        (
            "shopify",
            {"store_domain": "first.myshopify.com", "shop": "ignored.myshopify.com"},
            {"store_domain": "first.myshopify.com"},
        ),
        (
            "shopify",
            {"shop_domain": "legacy.myshopify.com", "shop": "ignored.myshopify.com"},
            {"store_domain": "legacy.myshopify.com"},
        ),
        ("shopify", {"shop": "legacy.myshopify.com"}, {"store_domain": "legacy.myshopify.com"}),
    ],
)
def test_project_probe_config_preserves_legacy_aliases_without_host_facts(
    connector, config, expected
):
    original = dict(config)
    source = {
        **config,
        "account_ref": "host-account",
        "readiness_groups": ["host-policy"],
        "permission_verification": {"enforcement": "local_required"},
    }
    assert project_probe_config(connector, source) == expected
    assert config == original


async def test_saved_method_http_and_rate_limiter_are_preserved():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["x-api-token"] == "synthetic-token"
        return httpx.Response(200, json={"success": True, "data": {"id": 4, "company_id": 9}})

    class Limiter:
        count = 0

        async def acquire(self, n=1):
            self.count += 1

    limiter = Limiter()
    auth = ConnectorAuth(
        method="api_token",
        fields={"api_token": "synthetic-token"},
        config={"company_domain": "acme.pipedrive.com"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await probe_credentials(
            "pipedrive", auth=auth, options=CallOptions(http=http, rate_limiter=limiter)
        )
        assert not http.is_closed
    assert result["metadata"]["evidence"]["account"]["provider_account_id"] == "9"
    assert len(seen) == 1
    assert limiter.count == 1


@pytest.mark.parametrize(
    "connector,method,fields,config,expected",
    [
        (
            "dataforseo",
            "basic",
            {"password": "fixture"},
            {"login": "account"},
            {"login": "account"},
        ),
        (
            "wordpress",
            "application_password",
            {"username": "author", "application_password": "fixture"},
            {"site_url": "https://wp.test"},
            {"site_url": "https://wp.test"},
        ),
        (
            "ghost",
            "admin_api_key",
            {"admin_api_key": "fixture"},
            {"base_url": "https://ghost.test", "api_version": "v5.0"},
            {"site_url": "https://ghost.test", "api_version": "v5.0"},
        ),
        (
            "shopify",
            "admin-api-token",
            {"admin_api_access_token": "fixture"},
            {"shop": "acme.myshopify.com", "api_version": "2026-07"},
            {"store_domain": "acme.myshopify.com", "api_version": "2026-07"},
        ),
        (
            "openrouter",
            "api_key",
            {"api_key": "fixture"},
            {"http_referer": " https://app.test ", "app_title": " App "},
            {"http_referer": "https://app.test", "app_title": "App"},
        ),
        (
            "pipedrive",
            "api_token",
            {"api_token": "fixture"},
            {"company_domain": "acme.pipedrive.com"},
            {"api_domain": "https://acme.pipedrive.com"},
        ),
        (
            "slack-bot",
            "bot-token",
            {"bot_token": "fixture"},
            {"api_base_url": "https://slack.test/api/"},
            {"api_base_url": "https://slack.test/api/"},
        ),
        (
            "trackbooth",
            "api-key",
            {"api_key": "fixture"},
            {"api_base_url": "https://track.test"},
            {"api_base_url": "https://track.test"},
        ),
        (
            "aws-s3",
            "aws-access-key",
            {"access_key_id": "fixture-id", "secret_access_key": "fixture"},
            {"bucket": "fixture-bucket", "region": "us-east-1", "prefix": "root/"},
            {"bucket": "fixture-bucket", "region": "us-east-1", "prefix": "root/"},
        ),
        (
            "ftp",
            "ftp-password",
            {"password": "fixture"},
            {"host": "ftp.test", "username": "user", "passive_mode": False, "timeout_s": 12.5},
            {
                "host": "ftp.test",
                "username": "user",
                "port": 21,
                "tls_mode": "explicit",
                "passive_mode": False,
                "timeout_s": 12.5,
                "encoding": "utf-8",
            },
        ),
        (
            "smtp",
            "smtp-password",
            {"password": "fixture"},
            {"host": "smtp.test", "username": "user", "port": 587, "tls_mode": "starttls"},
            {
                "host": "smtp.test",
                "username": "user",
                "port": 587,
                "tls_mode": "starttls",
                "timeout_s": 30,
            },
        ),
        (
            "imap",
            "imap-password",
            {"password": "fixture"},
            {
                "host": "imap.test",
                "username": "user",
                "port": 993,
                "tls_mode": "ssl",
                "tls_ca_pem": "synthetic-ca",
                "default_mailbox": "host-ref",
                "mailboxes": {"host-ref": "Secret"},
            },
            {
                "host": "imap.test",
                "username": "user",
                "port": 993,
                "tls_mode": "ssl",
                "timeout_s": 30,
                "tls_ca_pem": "synthetic-ca",
                "default_mailbox": "Native mailbox",
            },
        ),
    ],
)
async def test_provider_owned_constructor_variants(
    connector, method, fields, config, expected, monkeypatch
):
    metadata = get_default_client().registry.connector_metadata[connector]
    module_name, class_name = metadata["probe_implementation"].split(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    captured = {}

    def init(self, **kwargs):
        captured.update(kwargs)

    async def test(self):
        return {"ok": True}

    monkeypatch.setattr(cls, "__init__", init)
    monkeypatch.setattr(cls, "test_credentials", test)
    limiter = object()
    async with httpx.AsyncClient() as http:
        auth = ConnectorAuth(
            method=method, fields=fields, config={**config, "account_ref": "host-ref"}
        )
        assert (
            await probe_credentials(
                connector,
                auth=auth,
                options=CallOptions(
                    http=http, rate_limiter=limiter, provider_context={"mailbox": "Native mailbox"}
                ),
            )
        )["ok"]
        assert captured.pop("http") is http
        assert not http.is_closed
    assert captured.pop("rate_limiter") is limiter
    assert captured.pop("probe_context").auth_method_key == method
    assert captured.pop("timeout") is None
    payload = captured.pop("payload")
    declaration = next(d for d in metadata["auth_methods"] if d["key"] == method)
    assert payload == (
        str(fields[declaration["payload_field"]]).encode()
        if declaration["payload_format"] == "raw"
        else json.dumps(fields).encode()
    )
    assert captured == expected
    assert "host-ref" not in repr(captured)


@pytest.mark.parametrize(
    "connector,auth,context",
    [
        ("missing", ConnectorAuth(method="api_key", fields={"api_key": "synthetic"}), None),
        ("serper", ConnectorAuth(method="unknown", fields={"api_key": "synthetic"}), None),
        ("serper", ConnectorAuth(method="api_key", fields={}), None),
        (
            "serper",
            ConnectorAuth(method="api_key", fields={"api_key": "synthetic"}),
            AuthMethodProbeContext(auth_method_key="other"),
        ),
    ],
)
async def test_invalid_or_mismatched_probe_rejects_before_http(connector, auth, context):
    def handle(request):
        raise AssertionError("invalid probe must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValidationError):
            await probe_credentials(
                connector, auth=auth, context=context, options=CallOptions(http=http)
            )
        assert not http.is_closed


@pytest.mark.parametrize(
    "provider,fields",
    [
        ("google-ads", {"access_token": "resolved-token", "developer_token": "developer-token"}),
        ("google-workspace", {"access_token": "resolved-token"}),
    ],
)
async def test_acquisition_only_probe_reports_token_facts_without_acquiring(
    provider, fields, monkeypatch
):
    import stackos_connectors.auth as protocol

    async def forbidden(*args, **kwargs):
        raise AssertionError("probe must never acquire or refresh")

    monkeypatch.setattr(protocol, "request_token", forbidden)
    auth = ConnectorAuth(method="service-account", fields=fields)
    result = await probe_credentials(provider, auth=auth)
    assert result["ok"] is True
    assert result["metadata"] == {
        "verification": "resolved_token_only",
        "resource_access": "unverified",
    }
    assert "acquired" not in result["summary"].lower()
    assert "evidence" not in result["metadata"]


async def test_probe_redacts_exact_secret_echo_and_safe_provider_failure(monkeypatch):
    from stackos_connectors.connectors.serper.integration import SerperIntegration

    async def result(self):
        return {"ok": True, "metadata": {"echo": "canary-value", "api_key": "other-secret"}}

    monkeypatch.setattr(SerperIntegration, "test_credentials", result)
    auth = ConnectorAuth(method="api_key", fields={"api_key": "canary-value"})
    output = await probe_credentials("serper", auth=auth)
    assert "canary-value" not in json.dumps(output)
    assert "other-secret" not in json.dumps(output)

    async def failure(self):
        raise IntegrationDownError(
            "provider echoed canary-value",
            data={
                "status": 403,
                "provider_error": {
                    "error": {
                        "message": "canary-value",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                "reason": "SERVICE_DISABLED",
                            }
                        ],
                    }
                },
            },
        )

    from stackos_connectors.connectors.google_analytics.integration import (
        GoogleAnalyticsIntegration,
    )

    monkeypatch.setattr(GoogleAnalyticsIntegration, "test_credentials", failure)
    auth = ConnectorAuth(method="oauth2_access_token", fields={"access_token": "canary-value"})
    with pytest.raises(IntegrationDownError) as caught:
        await probe_credentials("google-analytics", auth=auth)
    assert "canary-value" not in str(caught.value)
    assert "canary-value" not in json.dumps(caught.value.data)
    assert caught.value.data["provider_reason"] == "SERVICE_DISABLED"
    assert caught.value.data["provider_api"] == "Google Analytics Admin API"
    assert caught.value.__cause__ is None


def test_probe_bindings_are_fixed_and_metadata_discovery_does_not_probe(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("discovery must not probe")

    monkeypatch.setattr("stackos_connectors.probe.probe_credentials", forbidden)
    description = get_default_client().describe("google-indexing")
    assert description["probe_implementation"].startswith("stackos_connectors.")
    assert description["auth_protocol"]["flow"] == "jwt_bearer"
    method = description["auth_methods"][0]
    assert method["evidence_source"] == "oauth_response"
    assert method["setup"]["auth_type"] == "oauth"
    assert not method["setup"]["interactive"]
    assert method["fields_schema"]["required"] == ["access_token"]
    # Every catalog-local binding is importable from the standalone wheel. Pure
    # config projection must neither construct a provider nor perform a probe.
    bound = {
        key
        for key, metadata in get_default_client().registry.connector_metadata.items()
        if metadata.get("probe_implementation")
    }
    assert len(bound) == 37
    assert all(project_probe_config(key, {}) == {} for key in bound)
