import asyncio
import json
from collections import deque
from pathlib import Path

import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.telegram.tdlib.native import (
    TelegramTdlibClient,
    TelegramTdlibClosedError,
)
from stackos_connectors.errors import ConnectorError


class ClosingABI:
    def __init__(self):
        self.requests, self.updates, self.destroyed = [], deque(), []

    def create_client(self):
        return 1

    def send(self, handle, payload):
        self.requests.append(json.loads(payload))
        self.updates.append(
            json.dumps(
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateClosed"},
                }
            ).encode()
        )

    def receive(self, handle, timeout):
        return self.updates.popleft() if self.updates else None

    def destroy_client(self, handle):
        self.destroyed.append(handle)


class Bound:
    files_directory = Path("/private/tmp")

    def __init__(self, native):
        self.native = native

    async def request(self, payload, *, timeout):
        return await self.native._request(
            payload, timeout_seconds=timeout, correlation_id="persisted-fixture"
        )

    async def wait_message(self, *args, **kwargs):
        raise AssertionError("No submission result")


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_closed_before_dispatch_vs_pending_native_close(started):
    abi = ClosingABI()
    native = TelegramTdlibClient(abi, receive_timeout_seconds=0.001)
    if started:
        await native.start()
    api = ConnectorClient(registry=load_registry("connectors/telegram/catalog.json"))
    with pytest.raises(ConnectorError) as failure:
        await api.execute(
            "telegram",
            "telegram.message.send",
            {
                "chat_id": 1,
                "topic_id": None,
                "reply_to": None,
                "reply_markup": None,
                "options": {"@type": "messageSendOptions"},
                "input_message_content": {"@type": "inputMessageText"},
            },
            ConnectorAuth("tdlib-bot-token", {}),
            CallOptions(native_session=Bound(native), correlation_id="persisted-fixture"),
        )
    metadata = failure.value.metadata_json
    assert metadata["provider_executed"] is started
    assert metadata["outcome_unknown"] is started
    assert metadata["provider_result_known"] is False
    assert metadata["retry_safe"] is False
    assert metadata["correlation_id"] == "persisted-fixture"
    assert len(abi.requests) == int(started)
    if started:
        assert abi.requests[0]["@extra"] == "persisted-fixture"
        assert abi.destroyed == [1]
        await asyncio.to_thread(native._receiver.join, 1)
        assert not native._receiver.is_alive()


@pytest.mark.asyncio
async def test_caller_closed_error_without_dispatch_fact_remains_unknown():
    class UnknownSession:
        async def request(self, payload, *, timeout):
            raise TelegramTdlibClosedError("closed")

    api = ConnectorClient(registry=load_registry("connectors/telegram/catalog.json"))
    with pytest.raises(ConnectorError) as failure:
        await api.execute(
            "telegram",
            "telegram.identity.get",
            {},
            ConnectorAuth("tdlib-bot-token", {}),
            CallOptions(native_session=UnknownSession()),
        )
    assert failure.value.metadata_json["outcome_unknown"] is True
    assert failure.value.metadata_json["retry_safe"] is False
