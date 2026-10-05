"""Exact protocol requests and side-effect receipts through public package calls."""

import hashlib
import json
import smtplib
from email.message import EmailMessage
from typing import ClassVar

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.imap import actions as imap
from stackos_connectors.connectors.smtp import actions as smtp
from stackos_connectors.errors import ConnectorError, ValidationError


@pytest.fixture
def client():
    return ConnectorClient(
        registry=load_registry(
            "connectors/smtp/catalog.json",
            "connectors/imap/catalog.json",
            "connectors/slack_bot/catalog.json",
        )
    )


def mail_auth(provider="smtp", mode="ssl"):
    return ConnectorAuth(
        method=provider + "-password",
        fields={"password": "fake-password"},
        config={
            "host": "mail.example.test",
            "port": 465 if provider == "smtp" else 993,
            "username": "sender@example.test",
            "tls_mode": mode,
        },
    )


def smtp_data():
    return {
        "from_email": "sender@example.test",
        "recipients": ["one@example.test", "two@example.test"],
        "subject": "hello",
        "text": "plain",
        "html": "<b>plain</b>",
        "bcc": ["hidden@example.test"],
    }


class SMTP:
    instances: ClassVar[list] = []
    refusal: ClassVar[dict] = {}
    failure = None

    def __init__(self, host, port, timeout=None):
        self.args = (host, port, timeout)
        self.calls = []
        self.__class__.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message, **kwargs):
        self.calls.append(("send", message, kwargs))
        if self.failure:
            raise self.failure
        return self.refusal

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


@pytest.mark.parametrize("mode", ["ssl", "starttls", "none"])
async def test_smtp_exact_submission_partial_and_timeout(client, monkeypatch, mode):
    SMTP.instances = []
    monkeypatch.setattr(SMTP, "refusal", {"two@example.test": (550, b"not accepted")})
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(smtp.smtplib, "SMTP", SMTP)
    result = await client.execute(
        "smtp", "smtp.email.send", smtp_data(), mail_auth(mode=mode), CallOptions(timeout=7)
    )
    connection = SMTP.instances[0]
    assert connection.args == ("mail.example.test", 465, 7)
    assert connection.calls[-2:] == ["quit", "close"]
    assert (
        connection.calls[:3] == ["ehlo", "starttls", "ehlo"]
        if mode == "starttls"
        else connection.calls[0] == ("login", "sender@example.test", "fake-password")
    )
    sends = [c for c in connection.calls if isinstance(c, tuple) and c[0] == "send"]
    assert len(sends) == 1
    message, kwargs = sends[0][1:]
    assert kwargs == {
        "from_addr": "sender@example.test",
        "to_addrs": ["one@example.test", "two@example.test", "hidden@example.test"],
    }
    assert message["Bcc"] is None and message.is_multipart()
    assert result.output_json["status"] == "partial"
    assert result.output_json["accepted_recipient_count"] == 2
    assert "message_ref" not in result.output_json
    assert result.metadata_json["retry_safe"] is False


@pytest.mark.parametrize(
    "failure", [OSError("lost after DATA"), smtplib.SMTPServerDisconnected("lost")]
)
async def test_smtp_unknown_submission_is_single_attempt_with_message_id(
    client, monkeypatch, failure
):
    SMTP.instances = []
    monkeypatch.setattr(SMTP, "failure", failure)
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", SMTP)
    with pytest.raises(ConnectorError) as failed:
        await client.execute("smtp", "smtp.email.send", smtp_data(), mail_auth())
    assert failed.value.output_json["message_id"]
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert len(SMTP.instances) == 1
    assert len([c for c in SMTP.instances[0].calls if isinstance(c, tuple) and c[0] == "send"]) == 1


async def test_smtp_total_refusal_is_factual_not_unknown(client, monkeypatch):
    SMTP.instances = []
    monkeypatch.setattr(
        SMTP,
        "failure",
        smtplib.SMTPRecipientsRefused(
            {
                a: (550, b"no")
                for a in ["one@example.test", "two@example.test", "hidden@example.test"]
            }
        ),
    )
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", SMTP)
    result = await client.execute("smtp", "smtp.email.send", smtp_data(), mail_auth())
    assert result.output_json["status"] == "rejected"
    assert result.output_json["accepted_recipient_count"] == 0


SLACK_CASES = [
    ("identity.get", "POST", "auth.test", {}, None),
    (
        "message.send",
        "POST",
        "chat.postMessage",
        {"channel": "C123", "text": "hello", "thread_ts": "123.45", "unfurl_links": False},
        {"channel": "C123", "text": "hello", "thread_ts": "123.45", "unfurl_links": False},
    ),
    (
        "reaction.add",
        "POST",
        "reactions.add",
        {"channel": "C123", "timestamp": "123.45", "name": "eyes"},
        {"channel": "C123", "timestamp": "123.45", "name": "eyes"},
    ),
    (
        "message.delete",
        "POST",
        "chat.delete",
        {"channel": "C123", "ts": "123.45"},
        {"channel": "C123", "ts": "123.45"},
    ),
    (
        "conversation.open",
        "POST",
        "conversations.open",
        {"users": ["U1", "U2"], "return_im": True},
        {"users": "U1,U2", "return_im": True},
    ),
    (
        "conversation.info",
        "GET",
        "conversations.info",
        {"channel": "C123", "include_num_members": True},
        {"channel": "C123", "include_num_members": "true"},
    ),
    (
        "conversation.list",
        "GET",
        "conversations.list",
        {
            "limit": 11,
            "cursor": "next",
            "types": ["public_channel", "im"],
            "exclude_archived": True,
        },
        {"limit": "11", "cursor": "next", "types": "public_channel,im", "exclude_archived": "true"},
    ),
    (
        "conversation.members",
        "GET",
        "conversations.members",
        {"channel": "C123", "limit": 11, "cursor": "next"},
        {"channel": "C123", "limit": "11", "cursor": "next"},
    ),
    (
        "conversation.history",
        "GET",
        "conversations.history",
        {"channel": "C123", "limit": 2, "oldest": "12.34", "inclusive": True},
        {"channel": "C123", "limit": "2", "oldest": "12.34", "inclusive": "true"},
    ),
]


@pytest.mark.parametrize("operation,method,route,data,expected", SLACK_CASES)
async def test_slack_exact_native_wire(client, operation, method, route, data, expected):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == method
        assert str(request.url).split("?")[0] == "https://slack.com/api/" + route
        assert request.headers["authorization"] == "Bearer xoxb-fake-only"
        assert request.extensions["timeout"] == {"connect": 9, "read": 9, "write": 9, "pool": 9}
        assert (
            dict(request.url.params)
            if method == "GET"
            else json.loads(request.content)
            if request.content
            else None
        ) == expected
        return httpx.Response(
            200,
            json={"ok": True, "channel": "C123", "ts": "123.45"},
            headers={"x-slack-req-id": "req-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=31) as http:
        result = await client.execute(
            "slack-bot",
            "slack-bot." + operation,
            data,
            ConnectorAuth("bot-token", {"bot_token": "xoxb-fake-only"}),
            CallOptions(http=http, timeout=9),
        )
        assert not http.is_closed and http.timeout.read == 31
    assert len(calls) == 1
    assert result.output_json["data"]["ts"] == "123.45"
    assert result.metadata_json["request_id"] == "req-1"


async def test_slack_upload_partial_receipt_and_no_duplicate(client, tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"bytes")
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            assert request.url.path == "/api/files.getUploadURLExternal"
            assert request.content == b"filename=data.bin&length=5"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file_id": "F1",
                    "upload_url": "https://upload.example.test/opaque",
                },
            )
        assert str(request.url) == "https://upload.example.test/opaque"
        assert request.content == b"bytes" and "authorization" not in request.headers
        raise httpx.ReadError("uncertain", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await client.execute(
                "slack-bot",
                "slack-bot.file.upload",
                {"channel": "C1", "files": [{"path": str(path), "filename": "data.bin"}]},
                ConnectorAuth("bot-token", {"bot_token": "xoxb-fake-only"}),
                CallOptions(http=http),
            )
    assert len(calls) == 2
    assert failed.value.output_json["reserved_file_ids"] == ["F1"]
    assert failed.value.output_json["stage"] == "upload_bytes"
    assert failed.value.metadata_json["retry_safe"] is False
    assert "opaque" not in str(failed.value.output_json)


class IMAP:
    instances: ClassVar[list] = []
    uidvalidity = "777"
    raw = b"Subject: example\r\n\r\ncontent"
    reported_size = None
    wrong_flags = False

    def __init__(self, host, port, *, ssl_context=None, timeout=None):
        self.args = (host, port, ssl_context, timeout)
        self.calls = []
        self.seen = False
        self.__class__.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def response(self, key):
        return key, [self.uidvalidity.encode()]

    def logout(self):
        self.calls.append(("logout",))

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def uid(self, *args):
        self.calls.append(args)
        if args[0] == "SEARCH":
            return "OK", [b"3 5"]
        if args[0] == "STORE":
            self.seen = args[2] == "+FLAGS"
            return "OK", []
        if args[2] == "(UID FLAGS)":
            flags = "\\Seen" if self.seen and not self.wrong_flags else ""
            return "OK", [f"1 (UID 3 FLAGS ({flags}))".encode()]
        size = self.reported_size if self.reported_size is not None else len(self.raw)
        if "BODY.PEEK" not in args[2]:
            return "OK", [f"1 (UID 3 RFC822.SIZE {size})".encode()]
        return "OK", [
            (f"1 (UID 3 FLAGS () RFC822.SIZE {size} BODY[] {{{len(self.raw)}}})".encode(), self.raw)
        ]


LIMITS = {
    "message_bytes": 10000,
    "attachments": 3,
    "attachment_bytes": 3000,
    "attachment_total_bytes": 5000,
    "mime_parts": 20,
    "mime_depth": 8,
}


@pytest.fixture
def imap_transport(monkeypatch):
    IMAP.instances = []
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", IMAP)
    return IMAP


@pytest.mark.parametrize(
    "operation,data,command",
    [
        ("mailbox.list", {}, None),
        (
            "messages.search",
            {"mailbox": "INBOX", "limit": 1, "criteria": {"unseen": True}},
            ("SEARCH", None, "UNSEEN"),
        ),
        (
            "message.fetch",
            {
                "mailbox": "INBOX",
                "uid": 3,
                "max_body_bytes": 64,
                "preview_chars": 500,
                "fields": ["subject", "flags", "text_preview"],
            },
            ("FETCH", "3", "(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.64>)"),
        ),
        (
            "message.mark_seen",
            {"mailbox": "INBOX", "uid": 3, "expected_uidvalidity": "777"},
            ("STORE", "3", "+FLAGS", "(\\Seen)"),
        ),
        (
            "message.mark_unseen",
            {"mailbox": "INBOX", "uid": 3, "expected_uidvalidity": "777"},
            ("STORE", "3", "-FLAGS", "(\\Seen)"),
        ),
    ],
)
async def test_imap_exact_native_commands(client, imap_transport, operation, data, command):
    result = await client.execute(
        "imap", "imap." + operation, data, mail_auth("imap"), CallOptions(timeout=7)
    )
    connection = IMAP.instances[0]
    assert connection.args[:2] == ("mail.example.test", 993) and connection.args[3] == 7
    assert connection.args[2].check_hostname
    assert connection.calls[0] == ("login", "sender@example.test", "fake-password")
    assert connection.calls[-1] == ("logout",)
    if command:
        assert command in connection.calls
    if operation.startswith("message.mark_"):
        assert connection.calls[-2] == ("FETCH", "3", "(UID FLAGS)")
    elif operation != "mailbox.list":
        assert ("select", "INBOX", True) in connection.calls
    assert "mailbox_ref" not in result.output_json


async def test_imap_export_exact_plain_files_readonly_and_caller_limits(
    client, imap_transport, tmp_path, monkeypatch
):
    message = EmailMessage()
    message.set_content("private")
    message.add_attachment(
        b"%PDF-fixture", maintype="application", subtype="pdf", filename="../../untrusted.pdf"
    )
    raw = message.as_bytes()
    monkeypatch.setattr(IMAP, "raw", raw)
    result = await client.execute(
        "imap",
        "imap.message.export",
        {"mailbox": "INBOX", "uid": 3, "limits": LIMITS},
        mail_auth("imap"),
        CallOptions(output_dir=tmp_path),
    )
    assert (tmp_path / "original.eml").read_bytes() == raw
    assert (tmp_path / "attachment-001").read_bytes() == b"%PDF-fixture"
    assert len(result.files) == 2
    assert result.output_json["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert all(
        value not in json.dumps(result.output_json)
        for value in ("private", "untrusted.pdf", "%PDF")
    )
    assert IMAP.instances[0].calls[1:4] == [
        ("select", "INBOX", True),
        ("FETCH", "3", "(UID RFC822.SIZE)"),
        ("FETCH", "3", "(UID RFC822.SIZE BODY.PEEK[])"),
    ]


async def test_imap_oversize_preflight_never_fetches_raw(
    client, imap_transport, tmp_path, monkeypatch
):
    monkeypatch.setattr(IMAP, "reported_size", LIMITS["message_bytes"] + 1)
    with pytest.raises(ConnectorError) as failed:
        await client.execute(
            "imap",
            "imap.message.export",
            {"mailbox": "INBOX", "uid": 3, "limits": LIMITS},
            mail_auth("imap"),
            CallOptions(output_dir=tmp_path),
        )
    assert failed.value.output_json["category"] == "oversize"
    assert not any("BODY.PEEK" in str(call) for call in IMAP.instances[0].calls)
    assert not list(tmp_path.iterdir())


async def test_imap_flag_unknown_readback_keeps_write_receipt(client, imap_transport, monkeypatch):
    monkeypatch.setattr(IMAP, "wrong_flags", True)
    with pytest.raises(ConnectorError) as failed:
        await client.execute(
            "imap",
            "imap.message.mark_seen",
            {"mailbox": "INBOX", "uid": 3, "expected_uidvalidity": "777"},
            mail_auth("imap"),
        )
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert sum(call[0] == "STORE" for call in IMAP.instances[0].calls) == 1


@pytest.mark.parametrize(
    "provider,action,data",
    [
        ("smtp", "smtp.email.send", smtp_data()),
        ("imap", "imap.mailbox.list", {}),
        ("slack-bot", "slack-bot.identity.get", {}),
    ],
)
async def test_missing_auth_no_protocol(client, monkeypatch, provider, action, data):
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("SMTP dispatched"))
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", lambda *a, **k: pytest.fail("IMAP dispatched"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("HTTP dispatched"))
    ) as http:
        with pytest.raises(ValidationError):
            await client.execute(provider, action, data, None, CallOptions(http=http))


@pytest.mark.parametrize(
    "key,value", [("message_bytes", 0), ("mime_depth", True), ("attachments", -1)]
)
async def test_invalid_explicit_export_limits_no_login(
    client, imap_transport, tmp_path, key, value
):
    with pytest.raises(ValidationError):
        await client.execute(
            "imap",
            "imap.message.export",
            {"mailbox": "INBOX", "uid": 3, "limits": {**LIMITS, key: value}},
            mail_auth("imap"),
            CallOptions(output_dir=tmp_path),
        )
    assert not IMAP.instances


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="not JSON"),
        httpx.Response(503, json={"ok": False, "error": "unavailable"}),
    ],
)
async def test_slack_mutation_malformed_or_503_keeps_single_uncertain_attempt(client, reply):
    calls = []

    def handle(request):
        calls.append(request)
        return reply

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await client.execute(
                "slack-bot",
                "slack-bot.message.send",
                {"channel": "C1", "text": "hello"},
                ConnectorAuth("bot-token", {"bot_token": "xoxb-fixture"}),
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False


async def test_slack_second_upload_failure_keeps_first_file_receipt(client, tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"bytes")
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={"ok": True, "file_id": "F1", "upload_url": "https://upload.example.test/F1"},
            )
        if len(calls) == 2:
            return httpx.Response(200, text="OK")
        return httpx.Response(
            200, json={"ok": False, "error": "missing_scope", "needed": "files:write"}
        )

    data = {
        "channel": "C1",
        "files": [
            {"path": str(path), "filename": "one.bin"},
            {"path": str(path), "filename": "two.bin"},
        ],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await client.execute(
                "slack-bot",
                "slack-bot.file.upload",
                data,
                ConnectorAuth("bot-token", {"bot_token": "xoxb-fixture"}),
                CallOptions(http=http),
            )
    assert len(calls) == 3
    assert failed.value.output_json["uploaded_files"][0]["id"] == "F1"
    assert failed.value.output_json["stage"] == "allocate_upload"
    assert failed.value.output_json["completed"] is False
    assert failed.value.metadata_json["retry_safe"] is False


async def test_imap_epoch_denial_records_selection_without_store(client, imap_transport):
    with pytest.raises(ConnectorError) as failed:
        await client.execute(
            "imap",
            "imap.message.mark_seen",
            {"mailbox": "INBOX", "uid": 3, "expected_uidvalidity": "999"},
            mail_auth("imap"),
        )
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["retry_safe"] is True
    assert not any(call[0] == "STORE" for call in IMAP.instances[0].calls)


@pytest.mark.parametrize("seen", [True, False])
@pytest.mark.parametrize("failure_stage", ["store_response", "readback_missing", "readback_error"])
async def test_imap_uncertain_flag_write_retains_observed_identity(
    client, imap_transport, monkeypatch, seen, failure_stage
):
    original_uid = IMAP.uid

    def fail_uid(self, *args):
        if args[0] == "STORE" and failure_stage == "store_response":
            self.calls.append(args)
            raise OSError("private-server-password not for receipt")
        if args[0] == "FETCH" and args[2] == "(UID FLAGS)":
            self.calls.append(args)
            if failure_stage == "readback_error":
                raise OSError("private-server-password not for receipt")
            if failure_stage == "readback_missing":
                return "OK", []
        return original_uid(self, *args)

    monkeypatch.setattr(IMAP, "uid", fail_uid)
    operation = "mark_seen" if seen else "mark_unseen"
    with pytest.raises(ConnectorError) as failed:
        await client.execute(
            "imap",
            "imap.message." + operation,
            {"mailbox": "INBOX", "uid": 3},
            mail_auth("imap"),
        )
    output = failed.value.output_json
    assert output["status"] == "failed"
    assert output["provider_error"]["outcome_unknown"] is True
    assert output["mailbox_name"] == "INBOX"
    assert output["uid"] == 3 and output["uidvalidity"] == "777"
    assert output["requested_seen"] is seen
    assert output["store_applied"] is None and output["store_confirmed"] is False
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert sum(call[0] == "STORE" for call in IMAP.instances[0].calls) == 1
    assert IMAP.instances[0].calls[-1] == ("logout",)
    assert "private-server-password" not in json.dumps(output)


async def test_imap_flag_validation_never_connects(client, imap_transport):
    with pytest.raises(ValidationError):
        await client.execute(
            "imap", "imap.message.mark_seen", {"mailbox": "INBOX", "uid": 0}, mail_auth("imap")
        )
    assert not IMAP.instances
