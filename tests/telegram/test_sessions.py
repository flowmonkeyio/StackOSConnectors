from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from stackos_connectors.connectors.telegram.tdlib.proxy import TelegramProxyConfig
from stackos_connectors.connectors.telegram.tdlib.sessions import (
    TelegramApplicationCredentials,
    TelegramTdlibSession,
    TelegramTdlibSessionConfig,
    TelegramTdlibSessionError,
)


class _Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.request_kwargs: list[dict[str, object]] = []
        self.proxies: list[dict[str, object]] = []
        self.updates: deque[dict[str, object]] = deque(
            [
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateWaitTdlibParameters"},
                }
            ]
        )
        self.close_calls = 0

    async def start(self) -> None:
        return None

    async def _request(self, request: dict[str, object], **kwargs: object) -> dict[str, object]:
        self.requests.append(request)
        self.request_kwargs.append(kwargs)
        kind = request["@type"]
        if kind == "getProxies":
            return {"@type": "addedProxies", "proxies": self.proxies}
        if kind == "addProxy":
            proxy = {
                "@type": "addedProxy",
                "id": 7,
                "is_enabled": True,
                "last_used_date": 0,
                "comment": "",
                "proxy": request["proxy"],
            }
            self.proxies = [proxy]
            return proxy
        return {"@type": "ok"}

    def _begin_request(self, request: dict[str, object]) -> asyncio.Future[dict[str, object]]:
        self.requests.append(request)
        loop = asyncio.get_running_loop()
        response: asyncio.Future[dict[str, object]] = loop.create_future()
        kind = request["@type"]
        if kind == "getProxies":
            response.set_result({"@type": "addedProxies", "proxies": self.proxies})
        else:
            response.set_result({"@type": "ok"})
        return response

    async def next_update(self) -> dict[str, object]:
        return self.updates.popleft()

    async def close(self, *, timeout_seconds: float) -> None:
        self.close_calls += 1


class _StalledClient(_Client):
    async def next_update(self) -> dict[str, object]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_session_queues_network_pause_and_proxy_before_tdlib_parameters(
    tmp_path: Path,
) -> None:
    client = _Client()
    session = TelegramTdlibSession(client_factory=lambda: client)
    config = TelegramTdlibSessionConfig(
        system_language_code="en",
        device_model="StackOS",
        system_version="macOS",
        application_version="StackOS",
        use_test_dc=False,
        use_file_database=True,
        use_chat_info_database=True,
        use_message_database=True,
        use_secret_chats=False,
        account_kind="bot",
        application=TelegramApplicationCredentials(api_id=12345, api_hash="application-secret"),
        database_directory=tmp_path / "database",
        files_directory=tmp_path / "files",
        database_encryption_key="storage-secret",
        proxy=TelegramProxyConfig(kind="socks5", host="127.0.0.1", port=1080),
    )

    receipt = await session.start(config)

    assert receipt.account_kind == "bot"
    assert receipt.proxy_id == 7
    assert [request["@type"] for request in client.requests] == [
        "setNetworkType",
        "getProxies",
        "setTdlibParameters",
        "addProxy",
        "getProxies",
        "setNetworkType",
    ]
    params = next(
        request for request in client.requests if request["@type"] == "setTdlibParameters"
    )
    assert params["api_hash"] == "application-secret"
    assert params["database_encryption_key"] == "c3RvcmFnZS1zZWNyZXQ="


@pytest.mark.asyncio
async def test_started_session_forwards_typed_request_with_persisted_correlation(
    tmp_path: Path,
) -> None:
    client = _Client()
    session = TelegramTdlibSession(client_factory=lambda: client)
    config = TelegramTdlibSessionConfig(
        system_language_code="en",
        device_model="StackOS",
        system_version="macOS",
        application_version="StackOS",
        use_test_dc=False,
        use_file_database=True,
        use_chat_info_database=True,
        use_message_database=True,
        use_secret_chats=False,
        account_kind="bot",
        application=TelegramApplicationCredentials(api_id=12345, api_hash="application-secret"),
        database_directory=tmp_path / "database",
        files_directory=tmp_path / "files",
        database_encryption_key="storage-secret",
    )
    await session.start(config)

    response = await session._request({"@type": "getMe"}, correlation_id="delivery-attempt-1")

    assert response == {"@type": "ok"}
    assert client.requests[-1] == {"@type": "getMe"}
    assert client.request_kwargs[-1] == {
        "timeout_seconds": 30.0,
        "correlation_id": "delivery-attempt-1",
    }


@pytest.mark.asyncio
async def test_session_startup_bounds_wait_for_tdlib_parameters(tmp_path: Path) -> None:
    client = _StalledClient()
    session = TelegramTdlibSession(client_factory=lambda: client)
    config = TelegramTdlibSessionConfig(
        system_language_code="en",
        device_model="StackOS",
        system_version="macOS",
        application_version="StackOS",
        use_test_dc=False,
        use_file_database=True,
        use_chat_info_database=True,
        use_message_database=True,
        use_secret_chats=False,
        account_kind="bot",
        application=TelegramApplicationCredentials(api_id=12345, api_hash="application-secret"),
        database_directory=tmp_path / "database",
        files_directory=tmp_path / "files",
        database_encryption_key="storage-secret",
    )

    with pytest.raises(TelegramTdlibSessionError, match="startup timed out"):
        await session.start(config, startup_timeout_seconds=0.001)

    assert client.close_calls == 1


def _caller_config(tmp_path: Path) -> TelegramTdlibSessionConfig:
    return TelegramTdlibSessionConfig(
        account_kind="user",
        application=TelegramApplicationCredentials(api_id=17, api_hash="caller-secret"),
        database_directory=tmp_path / "database",
        files_directory=tmp_path / "files",
        database_encryption_key="caller-key",
        system_language_code="fr",
        device_model="caller device",
        system_version="caller OS",
        application_version="2.3",
        use_test_dc=True,
        use_file_database=False,
        use_chat_info_database=False,
        use_message_database=False,
        use_secret_chats=True,
    )


def test_parameters_preserve_explicit_caller_choices(tmp_path: Path) -> None:
    config = _caller_config(tmp_path)
    assert config.tdlib_parameters() == {
        "@type": "setTdlibParameters",
        "api_id": 17,
        "api_hash": "caller-secret",
        "database_directory": str(tmp_path / "database"),
        "files_directory": str(tmp_path / "files"),
        "database_encryption_key": "Y2FsbGVyLWtleQ==",
        "system_language_code": "fr",
        "device_model": "caller device",
        "system_version": "caller OS",
        "application_version": "2.3",
        "use_test_dc": True,
        "use_file_database": False,
        "use_chat_info_database": False,
        "use_message_database": False,
        "use_secret_chats": True,
    }
    with pytest.raises(TelegramTdlibSessionError, match="boolean"):
        replace(config, use_secret_chats=1)


@pytest.mark.asyncio
async def test_failed_proxy_bootstrap_closes_without_network_resume(tmp_path: Path) -> None:
    class FailedProxy(_Client):
        async def _request(self, request, **kwargs):
            if request["@type"] == "addProxy":
                self.requests.append(request)
                raise TelegramTdlibSessionError("proxy rejected")
            return await super()._request(request, **kwargs)

    client = FailedProxy()
    session = TelegramTdlibSession(client_factory=lambda: client)
    config = replace(
        _caller_config(tmp_path),
        proxy=TelegramProxyConfig(kind="socks5", host="127.0.0.1", port=1080),
    )
    with pytest.raises(TelegramTdlibSessionError, match="proxy rejected"):
        await session.start(config)
    assert [request["@type"] for request in client.requests] == [
        "setNetworkType",
        "getProxies",
        "setTdlibParameters",
        "addProxy",
    ]
    assert client.requests[0]["type"] == {"@type": "networkTypeNone"}
    assert client.close_calls == 1
    with pytest.raises(TelegramTdlibSessionError, match="not started"):
        await session._request({"@type": "getMe"})
