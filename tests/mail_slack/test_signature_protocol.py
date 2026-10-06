import socket

import pytest

from stackos_connectors.connectors.slack_bot.auth import verify_signature_v0

_SIGNATURE = "v0=8429be9e0d7bdcafb444236fd24e321fcf4ec3f1d460db5a392366a784b25574"


def test_slack_v0_fixed_vector_and_caller_owned_timestamp(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("signature verification must not use a network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    # A cryptographically valid old timestamp remains valid here; replay is host policy.
    assert verify_signature_v0("test-secret", "1700000000", b'{"hello":"world"}', _SIGNATURE)


@pytest.mark.parametrize(
    "secret,timestamp,body,signature",
    [
        ("wrong", "1700000000", b'{"hello":"world"}', _SIGNATURE),
        ("test-secret", "1700000001", b'{"hello":"world"}', _SIGNATURE),
        ("test-secret", "1700000000", b'{"hello": "world"}', _SIGNATURE),
        ("test-secret", "1700000000", b'{"hello":"world"}', "v1=" + _SIGNATURE[3:]),
        ("test-secret", "1700000000", b'{"hello":"world"}', ""),
        ("test-secret", "1700000000", b'{"hello":"world"}', "non-ascii-\N{SNOWMAN}"),
    ],
)
def test_slack_signature_rejects_tampering_and_malformed_input(secret, timestamp, body, signature):
    assert not verify_signature_v0(secret, timestamp, body, signature)
