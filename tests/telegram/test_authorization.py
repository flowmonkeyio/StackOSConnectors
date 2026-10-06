from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError

import pytest

from stackos_connectors.connectors.telegram.auth import (
    CHALLENGE_FIELDS,
    challenge_request,
    initial_request,
    is_saved_authorization_rejected,
    parse_authorization_state,
    sanitize_challenge_metadata,
)
from stackos_connectors.connectors.telegram.tdlib.native import TelegramTdlibRequestError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("authorization translation must not use a network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def test_initial_requests_are_explicit_data_only():
    assert initial_request("bot", "phone", "secret-token") == {
        "@type": "checkAuthenticationBotToken",
        "token": "secret-token",
    }
    assert initial_request("user", "qr", None) == {
        "@type": "requestQrCodeAuthentication",
        "other_user_ids": [],
    }
    assert initial_request("user", "phone", None) is None
    with pytest.raises(ValueError, match="missing its token"):
        initial_request("bot", "phone", None)


@pytest.mark.parametrize(
    ("kind", "answer", "expected"),
    [
        (
            "phone_number",
            {"phone_number": "+15550000123"},
            {
                "@type": "setAuthenticationPhoneNumber",
                "phone_number": "+15550000123",
                "settings": None,
            },
        ),
        ("code", {"code": "123456"}, {"@type": "checkAuthenticationCode", "code": "123456"}),
        (
            "password",
            {"password": "sentinel-password"},
            {
                "@type": "checkAuthenticationPassword",
                "password": "sentinel-password",
            },
        ),
        (
            "email_address",
            {"email_address": "test@example.test"},
            {
                "@type": "setAuthenticationEmailAddress",
                "email_address": "test@example.test",
            },
        ),
        (
            "email_code",
            {"code": "123456"},
            {
                "@type": "checkAuthenticationEmailCode",
                "code": {"@type": "emailAddressAuthenticationCode", "code": "123456"},
            },
        ),
        (
            "registration",
            {"first_name": "Test"},
            {
                "@type": "registerUser",
                "first_name": "Test",
                "last_name": "",
            },
        ),
    ],
)
def test_challenge_requests_preserve_protocol(kind, answer, expected):
    original = dict(answer)
    assert challenge_request(kind, answer) == expected
    assert answer == original


@pytest.mark.parametrize(
    "kind,answer",
    [
        ("qr", {"sentinel-secret": "answer"}),
        ("code", {"code": {"sentinel-secret": "answer"}}),
        ("code", "sentinel-secret"),
        ("registration", {"first_name": "Test", "last_name": {"sentinel-secret": "answer"}}),
    ],
)
def test_invalid_answers_have_safe_errors(kind, answer):
    with pytest.raises(ValueError) as caught:
        challenge_request(kind, answer)
    assert "sentinel-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "suffix,kind",
    [
        ("PhoneNumber", "phone_number"),
        ("Code", "code"),
        ("Password", "password"),
        ("EmailAddress", "email_address"),
        ("EmailCode", "email_code"),
        ("OtherDeviceConfirmation", "qr"),
        ("Registration", "registration"),
    ],
)
def test_state_facts_have_no_lifecycle_decisions(suffix, kind):
    facts = parse_authorization_state({"@type": "authorizationStateWait" + suffix})
    assert facts.category == "challenge"
    assert facts.challenge_kind == kind
    assert facts.challenge_fields == CHALLENGE_FIELDS[kind]
    assert not hasattr(facts, "expires_at")
    assert not hasattr(facts, "generation")
    assert not hasattr(facts, "status")
    with pytest.raises(FrozenInstanceError):
        facts.category = "ready"
    with pytest.raises(TypeError):
        CHALLENGE_FIELDS[kind] = ()


@pytest.mark.parametrize(
    "raw,category",
    [
        ({}, "invalid"),
        ({"@type": 123}, "invalid"),
        ({"@type": "authorizationStateReady"}, "ready"),
        ({"@type": "authorizationStateClosing"}, "closed"),
        ({"@type": "authorizationStateClosed"}, "closed"),
        ({"@type": "authorizationStateUnknown"}, "unsupported"),
    ],
)
def test_non_challenge_categories_are_protocol_facts(raw, category):
    facts = parse_authorization_state(raw)
    assert facts.category == category
    assert facts.challenge_kind is None
    assert facts.qr_link is None


def test_safe_metadata_and_qr_remain_separate_and_immutable():
    facts = parse_authorization_state(
        {
            "@type": "authorizationStateWaitOtherDeviceConfirmation",
            "link": "tg://login?token=sentinel-qr-secret",
            "code_info": {
                "timeout": 900,
                "type": {"@type": "authenticationCodeTypeSms"},
                "phone_number": "sentinel-phone",
            },
            "allow_apple_id": True,
            "allow_google_id": False,
            "email": "sentinel-email",
        }
    )
    assert facts.qr_link == "tg://login?token=sentinel-qr-secret"
    assert dict(facts.metadata) == {
        "timeout_seconds": 900,
        "delivery_type": "authenticationCodeTypeSms",
        "allow_apple_id": True,
        "allow_google_id": False,
    }
    assert "sentinel" not in repr(facts)
    with pytest.raises(TypeError):
        facts.metadata["timeout_seconds"] = 1
    assert (
        parse_authorization_state(
            {"@type": "authorizationStateWaitOtherDeviceConfirmation", "link": "https://bad.test"}
        ).qr_link
        is None
    )
    assert (
        sanitize_challenge_metadata(
            {"timeout_seconds": True, "delivery_type": "sentinel-secret", "allow_apple_id": "yes"}
        )
        == {}
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("AUTH_KEY_UNREGISTERED", True),
        ("SESSION_REVOKED", True),
        ("BOT_TOKEN_INVALID", True),
        ("TOKEN_INVALID", True),
        ("USER_DEACTIVATED", True),
        ("USER_DEACTIVATED_BAN", True),
        ("PHONE_CODE_INVALID", False),
        ("FLOOD_WAIT", False),
        (None, False),
    ],
)
def test_saved_authorization_classifies_only_explicit_protocol_errors(name, expected):
    error = TelegramTdlibRequestError(code=401, phase="request", error_name=name)
    assert is_saved_authorization_rejected(error) is expected
    assert not is_saved_authorization_rejected(RuntimeError("SESSION_REVOKED"))
