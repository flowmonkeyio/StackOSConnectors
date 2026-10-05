"""Provider-edge tests have no application database, project or credential fixtures."""

import socket

import pytest


@pytest.fixture(autouse=True)
def fast_protocol_waits(monkeypatch):
    async def no_wait(*args, **kwargs):
        return None

    monkeypatch.setattr("stackos_connectors.integrations._base.asyncio.sleep", no_wait)
    monkeypatch.setattr("stackos_connectors.integrations._rate_limit.TokenBucket.acquire", no_wait)


@pytest.fixture(autouse=True)
def reject_unmocked_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("provider test attempted an unmocked network connection")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)
