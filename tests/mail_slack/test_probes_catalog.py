"""Standalone probe, discovery and no-host-state contracts."""

from importlib.resources import files

import httpx
import pytest

from stackos_connectors import ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.imap import integration as imap_probe
from stackos_connectors.connectors.slack_bot.integration import SlackBotIntegration
from stackos_connectors.connectors.smtp import integration as smtp_probe
from stackos_connectors.errors import IntegrationDownError, ValidationError


@pytest.mark.parametrize("provider,expected_count", [("smtp", 1), ("imap", 6), ("slack-bot", 10)])
def test_catalog_native_actions_auth_and_bundled_icons(provider, expected_count):
    directory = provider.replace("-", "_")
    client = ConnectorClient(registry=load_registry(f"connectors/{directory}/catalog.json"))
    description = client.describe(provider)
    assert len(description["actions"]) == expected_count
    assert files("stackos_connectors").joinpath(description["icon"]["path"]).read_bytes()
    assert all(
        action["name"] and action["description"] and action["icon"] == description["icon"]
        for action in description["actions"]
    )
    for action in description["actions"]:
        fields = action["input_schema"]["properties"]
        assert not any(
            key in fields
            for key in [
                "profile_ref",
                "mailbox_ref",
                "artifact_ref",
                "from_ref",
                "source_agent_request_id",
                "transfer_id",
            ]
        )
        assert action["input_schema"]["additionalProperties"] is False
    auth = description["auth_methods"][0]
    assert "signing_secret" not in auth["fields_schema"]["properties"]
    assert not any(
        k in auth["config_schema"]["properties"]
        for k in [
            "mailbox_refs",
            "from_refs",
            "allowed_reply_to",
            "default_mailbox",
            "search_limit",
        ]
    )


async def test_smtp_probe_exact_login_and_cleanup(monkeypatch):
    calls = []

    class Session:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self):
            calls.append("starttls")

        def login(self, u, p):
            calls.append(("login", u, p))

        def quit(self):
            calls.append("quit")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(smtp_probe.smtplib, "SMTP", Session)
    async with httpx.AsyncClient() as http:
        result = await smtp_probe.SmtpIntegration(
            payload=b'{"password":"synthetic-password"}',
            http=http,
            host="smtp.example.test",
            port=587,
            tls_mode="starttls",
            username="fixture",
            timeout_s=4,
        ).test_credentials()
    assert result["ok"] is True
    assert calls == [
        ("connect", "smtp.example.test", 587, 4),
        "ehlo",
        "starttls",
        "ehlo",
        ("login", "fixture", "synthetic-password"),
        "quit",
        "close",
    ]


async def test_imap_probe_exact_readonly_select_and_no_expunge(monkeypatch):
    calls = []

    class Session:
        def __init__(self, host, port, ssl_context, timeout):
            assert ssl_context.check_hostname
            calls.append(("connect", host, port, timeout))

        def login(self, u, p):
            calls.append(("login", u, p))

        def select(self, mailbox, readonly):
            calls.append(("select", mailbox, readonly))
            return "OK", []

        def logout(self):
            calls.append("logout")

        def close(self):
            pytest.fail("CLOSE would expunge unrelated deleted mail")

    monkeypatch.setattr(imap_probe.imaplib, "IMAP4_SSL", Session)
    async with httpx.AsyncClient() as http:
        result = await imap_probe.ImapIntegration(
            payload=b'{"password":"synthetic-password"}',
            http=http,
            host="imap.example.test",
            port=993,
            tls_mode="ssl",
            username="fixture",
            default_mailbox="Receipts",
            timeout_s=4,
        ).test_credentials()
    assert result["ok"] is True
    assert calls == [
        ("connect", "imap.example.test", 993, 4),
        ("login", "fixture", "synthetic-password"),
        ("select", "Receipts", True),
        "logout",
    ]


async def test_slack_probe_exact_auth_request_and_identity():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == "https://slack.com/api/auth.test"
        assert request.headers["authorization"] == "Bearer xoxb-fixture"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(200, json={"ok": True, "team_id": "T1", "bot_id": "B1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await SlackBotIntegration(
            payload=b'{"bot_token":"xoxb-fixture"}', http=http
        ).test_credentials()
    assert result["ok"] is True and result["team_id"] == "T1" and len(calls) == 1


@pytest.mark.parametrize(
    "value", ["not PEM", "-----BEGIN PRIVATE KEY-----\nnot-a-cert\n-----END PRIVATE KEY-----", 17]
)
def test_native_ca_rejects_invalid_bundle(value):
    with pytest.raises(ValidationError):
        imap_probe.imap_ssl_context(value)


@pytest.mark.parametrize("provider", ["smtp", "imap", "slack"])
async def test_empty_probe_credentials_reject_before_transport(provider):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP"))
    ) as http:
        with pytest.raises(IntegrationDownError):
            if provider == "slack":
                SlackBotIntegration(payload=b"", http=http)
            else:
                klass = (
                    smtp_probe.SmtpIntegration if provider == "smtp" else imap_probe.ImapIntegration
                )
                klass(
                    payload=b"",
                    http=http,
                    host="example.test",
                    port=1,
                    tls_mode="ssl",
                    username="fixture",
                )
