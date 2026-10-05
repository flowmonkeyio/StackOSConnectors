"""One TDLib session with explicit caller parameters and ordered proxy bootstrap."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from stackos_connectors.connectors.telegram.tdlib.proxy import (
    TelegramProxyConfig,
    TelegramProxyController,
)


class TelegramTdlibSessionError(RuntimeError):
    """A TDLib client didn't follow the bootstrap state contract."""


@dataclass(frozen=True)
class TelegramApplicationCredentials:
    """Telegram application credentials, supplied by the caller."""

    api_id: int
    api_hash: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.api_id, int) or isinstance(self.api_id, bool) or self.api_id <= 0:
            raise TelegramTdlibSessionError(
                "Telegram application api_id must be a positive integer"
            )
        if not isinstance(self.api_hash, str) or not self.api_hash:
            raise TelegramTdlibSessionError("Telegram application api_hash is required")


@dataclass(frozen=True)
class TelegramTdlibSessionConfig:
    """Non-persisting session configuration for either a bot or user account."""

    account_kind: Literal["bot", "user"]
    application: TelegramApplicationCredentials
    database_directory: Path
    files_directory: Path
    database_encryption_key: str = field(repr=False)
    system_language_code: str
    device_model: str
    system_version: str
    application_version: str
    use_test_dc: bool
    use_file_database: bool
    use_chat_info_database: bool
    use_message_database: bool
    use_secret_chats: bool
    proxy: TelegramProxyConfig | None = None
    persisted_proxy_id: int | None = None

    def __post_init__(self) -> None:
        if self.account_kind not in {"bot", "user"}:
            raise TelegramTdlibSessionError("Telegram account kind must be bot or user")
        if not isinstance(self.database_encryption_key, str) or not self.database_encryption_key:
            raise TelegramTdlibSessionError("TDLib database encryption key is required")
        for directory_label, directory in (
            ("database directory", self.database_directory),
            ("files directory", self.files_directory),
        ):
            if not isinstance(directory, Path) or not directory.is_absolute():
                raise TelegramTdlibSessionError(f"TDLib {directory_label} must be an absolute path")
        for metadata_label, metadata_value in (
            ("system language code", self.system_language_code),
            ("device model", self.device_model),
            ("system version", self.system_version),
            ("application version", self.application_version),
        ):
            if not isinstance(metadata_value, str) or not metadata_value:
                raise TelegramTdlibSessionError(f"TDLib {metadata_label} is required")
        for name in (
            "use_test_dc",
            "use_file_database",
            "use_chat_info_database",
            "use_message_database",
            "use_secret_chats",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TelegramTdlibSessionError(f"TDLib {name} must be a boolean")
        if self.persisted_proxy_id is not None and self.persisted_proxy_id <= 0:
            raise TelegramTdlibSessionError("persisted TDLib proxy id must be positive")

    def tdlib_parameters(self) -> dict[str, Any]:
        return {
            "@type": "setTdlibParameters",
            "use_test_dc": self.use_test_dc,
            "database_directory": str(self.database_directory),
            "files_directory": str(self.files_directory),
            # TDLib's JSON ABI represents its ``bytes`` field as base64.  The
            # caller secret remains an opaque UTF-8 value and is never logged.
            "database_encryption_key": base64.b64encode(
                self.database_encryption_key.encode("utf-8")
            ).decode("ascii"),
            "use_file_database": self.use_file_database,
            "use_chat_info_database": self.use_chat_info_database,
            "use_message_database": self.use_message_database,
            "use_secret_chats": self.use_secret_chats,
            "api_id": self.application.api_id,
            "api_hash": self.application.api_hash,
            "system_language_code": self.system_language_code,
            "device_model": self.device_model,
            "system_version": self.system_version,
            "application_version": self.application_version,
        }


@dataclass(frozen=True)
class TelegramTdlibSessionReceipt:
    account_kind: Literal["bot", "user"]
    proxy_id: int | None
    proxy_enabled: bool


class _TdlibClient(Protocol):
    async def start(self) -> None: ...

    async def _request(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    def _begin_request(
        self,
        request: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> Awaitable[dict[str, Any]]: ...

    async def next_update(self) -> dict[str, Any]: ...

    async def close(self, *, timeout_seconds: float) -> None: ...


class TelegramTdlibSession:
    """A reusable ordered session wrapper; bot and user auth both continue from it."""

    def __init__(self, *, client_factory: Callable[[], _TdlibClient]) -> None:
        self._client_factory = client_factory
        self._client: _TdlibClient | None = None
        self._started = False

    async def start(
        self, config: TelegramTdlibSessionConfig, *, startup_timeout_seconds: float = 30.0
    ) -> TelegramTdlibSessionReceipt:
        if self._started:
            raise TelegramTdlibSessionError("TDLib session is already started")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        client = self._client_factory()
        await client.start()
        try:
            async with asyncio.timeout(startup_timeout_seconds):
                await self._wait_for_parameters_state(client)
                # TDLib queues preauthorization requests until it creates a
                # ConnectionCreator. Queue a network pause first, then the known
                # proxy-state snapshot, and don't await either before setTdlibParameters.
                # The pinned source's Td::init_options_and_network drains both kinds
                # in order before it constructs the remaining auth managers.
                network_paused = client._begin_request(
                    {"@type": "setNetworkType", "type": {"@type": "networkTypeNone"}}
                )
                proxy_controller = TelegramProxyController(client)
                proxy_snapshot = proxy_controller.begin_snapshot()
                response = await client._request(config.tdlib_parameters())
                if response.get("@type") != "ok":
                    raise TelegramTdlibSessionError("TDLib rejected initialization parameters")
                if (await network_paused).get("@type") != "ok":
                    raise TelegramTdlibSessionError("TDLib rejected the startup network pause")
                proxy = await proxy_controller.reconcile_from(
                    await proxy_snapshot.resolve(),
                    config.proxy,
                    persisted_proxy_id=config.persisted_proxy_id,
                )
                network_resumed = await client._request(
                    {"@type": "setNetworkType", "type": {"@type": "networkTypeOther"}}
                )
                if network_resumed.get("@type") != "ok":
                    raise TelegramTdlibSessionError("TDLib rejected the startup network resume")
        except TimeoutError as exc:
            await client.close(timeout_seconds=10.0)
            raise TelegramTdlibSessionError("TDLib session startup timed out") from exc
        except Exception:
            await client.close(timeout_seconds=10.0)
            raise
        self._client = client
        self._started = True
        return TelegramTdlibSessionReceipt(
            account_kind=config.account_kind,
            proxy_id=proxy.proxy_id,
            proxy_enabled=proxy.enabled,
        )

    async def next_update(self) -> dict[str, Any]:
        if self._client is None:
            raise TelegramTdlibSessionError("TDLib session is not started")
        return await self._client.next_update()

    async def _request(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Forward a typed TDLib request through this live session.

        Callers can supply their request correlation through
        ``correlation_id``.  They must not place ``@extra`` directly in the
        request: the native client owns collision detection and JSON correlation.
        """
        if self._client is None:
            raise TelegramTdlibSessionError("TDLib session is not started")
        return await self._client._request(
            request, timeout_seconds=timeout_seconds, correlation_id=correlation_id
        )

    async def close(self, *, timeout_seconds: float = 10.0) -> None:
        if self._client is None:
            return
        try:
            await self._client.close(timeout_seconds=timeout_seconds)
        finally:
            self._client = None
            self._started = False

    async def _wait_for_parameters_state(self, client: _TdlibClient) -> None:
        while True:
            update = await client.next_update()
            if update.get("@type") != "updateAuthorizationState":
                continue
            authorization_state = update.get("authorization_state")
            if not isinstance(authorization_state, Mapping):
                raise TelegramTdlibSessionError("TDLib returned an invalid authorization update")
            if authorization_state.get("@type") == "authorizationStateWaitTdlibParameters":
                return
            if authorization_state.get("@type") == "authorizationStateClosed":
                raise TelegramTdlibSessionError("TDLib closed before initialization")


__all__ = [
    "TelegramApplicationCredentials",
    "TelegramTdlibSession",
    "TelegramTdlibSessionConfig",
    "TelegramTdlibSessionError",
    "TelegramTdlibSessionReceipt",
]
