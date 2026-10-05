import socket

import pytest


@pytest.fixture
def project_id():
    # Legacy test fixture identifier is never passed into the portable client.
    return 1


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Live sockets are forbidden in connector tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
