import json
from pathlib import Path

import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.errors import ConnectorError, ValidationError

AUTH = ConnectorAuth("tdlib-bot-token", {})


def client():
    return ConnectorClient(registry=load_registry("connectors/telegram/catalog.json"))


def send_data(*, album=False):
    data = {
        "chat_id": 123,
        "topic_id": None,
        "reply_to": None,
        "options": {"@type": "messageSendOptions", "sending_id": 7},
    }
    content = {
        "@type": "inputMessageText",
        "text": {"@type": "formattedText", "text": "private body", "entities": []},
    }
    if album:
        data["input_message_contents"] = [content, content]
    else:
        data["reply_markup"] = None
        data["input_message_content"] = content
    return data


class Session:
    def __init__(self, directory, *, album=False, timeout_second=False, result=None):
        self.files_directory = directory
        self.calls = []
        self.waits = []
        self.events = []
        self.album = album
        self.timeout_second = timeout_second
        self.result = result

    async def request(self, payload, *, timeout=30.0):
        self.calls.append((payload, timeout))
        if self.result is not None:
            return self.result
        messages = [
            {
                "@type": "message",
                "id": -1,
                "chat_id": 123,
                "sending_state": {"@type": "messageSendingStatePending"},
            }
        ]
        if self.album:
            messages.append({**messages[0], "id": -2})
            return {"@type": "messages", "messages": messages}
        return messages[0]

    async def wait_message(self, chat_id, temporary_id, *, timeout):
        self.waits.append((chat_id, temporary_id, timeout))
        self.events.append(("wait", temporary_id))
        if temporary_id == -2 and self.timeout_second:
            raise TimeoutError("private timeout detail")
        return {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": temporary_id,
            "message": {
                "@type": "message",
                "id": 100 - temporary_id,
                "chat_id": chat_id,
                "content": {"@type": "messageText", "text": {"text": "private body"}},
            },
        }


@pytest.mark.asyncio
async def test_fixed_named_call_exact_body_timeout_and_no_route_override(tmp_path):
    session = Session(tmp_path, result={"@type": "user", "id": 42})
    result = await client().execute(
        "telegram",
        "telegram.identity.get",
        {},
        AUTH,
        CallOptions(native_session=session, timeout=17),
    )
    assert session.calls == [({"@type": "getMe"}, 17)]
    assert result.output_json == {"@type": "user", "id": 42}
    with pytest.raises(ValidationError):
        await client().execute(
            "telegram",
            "telegram.identity.get",
            {"@type": "sendMessage"},
            AUTH,
            CallOptions(native_session=session),
        )
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_no_bound_session_cannot_execute():
    with pytest.raises(ValidationError) as error:
        await client().execute("telegram", "telegram.identity.get", {}, AUTH)
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.asyncio
async def test_send_joins_native_final_receipt_without_resubmission(tmp_path):
    session = Session(tmp_path)
    result = await client().execute(
        "telegram", "telegram.message.send", send_data(), AUTH, CallOptions(native_session=session)
    )
    assert len(session.calls) == 1
    assert session.calls[0][0] == {"@type": "sendMessage", **send_data()}
    assert session.waits == [(123, -1, 60.0)]
    assert result.output_json["messages"][0]["id"] == 101
    assert result.output_json["temporary_message_ids"] == [-1]


@pytest.mark.asyncio
async def test_partial_album_reports_confirmed_member_before_later_timeout(tmp_path):
    session = Session(tmp_path, album=True, timeout_second=True)

    def progress(event):
        if event["phase"] == "message_confirmed":
            session.events.append(("stored", event["message"]["id"]))

    with pytest.raises(ConnectorError) as error:
        await client().execute(
            "telegram",
            "telegram.album.send",
            send_data(album=True),
            AUTH,
            CallOptions(native_session=session, progress_callback=progress),
        )
    assert session.events == [("wait", -1), ("stored", 101), ("wait", -2)]
    assert len(session.calls) == 1
    assert error.value.metadata_json["confirmed_messages"] == [{"chat_id": 123, "message_id": 101}]
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["outcome_unknown"] is True
    assert "private body" not in str(error.value.metadata_json)
    assert "private timeout" not in str(error.value)


@pytest.mark.asyncio
async def test_failed_storage_callback_keeps_confirmed_native_receipt(tmp_path):
    session = Session(tmp_path)

    def progress(event):
        if event["phase"] == "message_confirmed":
            raise RuntimeError("private storage details")

    with pytest.raises(ConnectorError) as error:
        await client().execute(
            "telegram",
            "telegram.message.send",
            send_data(),
            AUTH,
            CallOptions(native_session=session, progress_callback=progress),
        )
    assert len(session.calls) == 1
    assert error.value.metadata_json["provider_executed"] is True
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["confirmed_messages"] == [{"chat_id": 123, "message_id": 101}]
    assert error.value.metadata_json["provider_result_known"] is True
    assert error.value.metadata_json["outcome_unknown"] is False
    assert "private" not in json.dumps(error.value.metadata_json)


@pytest.mark.asyncio
async def test_download_rejects_symlink_outside_bound_session(tmp_path):
    directory = tmp_path / "account"
    directory.mkdir()
    private = tmp_path / "other-account.txt"
    private.write_text("private")
    link = directory / "file.txt"
    link.symlink_to(private)
    session = Session(
        directory,
        result={
            "@type": "file",
            "id": 5,
            "local": {"is_downloading_completed": True, "path": str(link)},
        },
    )
    with pytest.raises(ConnectorError, match="outside"):
        await client().execute(
            "telegram",
            "telegram.file.download",
            {"file_id": 5, "priority": 16, "offset": 0, "limit": 0, "synchronous": True},
            AUTH,
            CallOptions(native_session=session),
        )
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_native_download_requires_completed_contained_file(tmp_path):
    file = tmp_path / "file.txt"
    file.write_text("fixture")
    session = Session(
        tmp_path,
        result={
            "@type": "file",
            "id": 5,
            "local": {"is_downloading_completed": True, "path": str(file)},
        },
    )
    data = {"file_id": 5, "priority": 16, "offset": 0, "limit": 0, "synchronous": True}
    result = await client().execute(
        "telegram", "telegram.file.download", data, AUTH, CallOptions(native_session=session)
    )
    assert len(result.files) == 1
    assert Path(result.files[0].path).read_text() == "fixture"
    assert result.files[0].size_bytes == 7
    session.result["local"]["is_downloading_completed"] = False
    with pytest.raises(ConnectorError, match="did not complete"):
        await client().execute(
            "telegram", "telegram.file.download", data, AUTH, CallOptions(native_session=session)
        )
    session.result["local"] = {
        "is_downloading_completed": True,
        "path": str(tmp_path.parent / "other-account.txt"),
    }
    with pytest.raises(ConnectorError, match="outside"):
        await client().execute(
            "telegram", "telegram.file.download", data, AUTH, CallOptions(native_session=session)
        )


@pytest.mark.asyncio
async def test_missing_local_upload_is_denied_before_native_request(tmp_path):
    session = Session(tmp_path)
    data = send_data()
    data["input_message_content"] = {
        "@type": "inputMessageDocument",
        "document": {
            "@type": "inputDocument",
            "document": {"@type": "inputFileLocal", "path": str(tmp_path / "missing")},
        },
    }
    with pytest.raises(ValidationError):
        await client().execute(
            "telegram", "telegram.message.send", data, AUTH, CallOptions(native_session=session)
        )
    assert session.calls == []
