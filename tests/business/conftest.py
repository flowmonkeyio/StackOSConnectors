import socket

import pytest


@pytest.fixture(autouse=True)
def prohibit_live_provider_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("live network is forbidden in connector tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
