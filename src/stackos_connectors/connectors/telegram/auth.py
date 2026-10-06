"""Pure TDLib authorization translations; callers own sending and session policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from .tdlib.native import TelegramTdlibRequestError

_STATE_CHALLENGES = {
    "authorizationStateWaitPhoneNumber": "phone_number",
    "authorizationStateWaitCode": "code",
    "authorizationStateWaitPassword": "password",
    "authorizationStateWaitEmailAddress": "email_address",
    "authorizationStateWaitEmailCode": "email_code",
    "authorizationStateWaitOtherDeviceConfirmation": "qr",
    "authorizationStateWaitRegistration": "registration",
}
CHALLENGE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "phone_number": ("phone_number",),
        "code": ("code",),
        "password": ("password",),
        "email_address": ("email_address",),
        "email_code": ("code",),
        "qr": (),
        "registration": ("first_name", "last_name"),
    }
)
_SAVED_AUTH_REJECTIONS = frozenset(
    {
        "AUTH_KEY_UNREGISTERED",
        "SESSION_REVOKED",
        "BOT_TOKEN_INVALID",
        "TOKEN_INVALID",
        "USER_DEACTIVATED",
        "USER_DEACTIVATED_BAN",
    }
)


@dataclass(frozen=True)
class AuthorizationState:
    """Provider facts only; ``qr_link`` is sensitive and must not be persisted."""

    category: Literal["invalid", "ready", "closed", "unsupported", "challenge"]
    state_type: str = field(repr=False)
    challenge_kind: str | None = None
    challenge_fields: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    qr_link: str | None = field(default=None, repr=False)


def initial_request(
    account_kind: str, authorization_mode: str, bot_token: str | None
) -> dict[str, Any] | None:
    """Build an explicitly requested initial message without sending or restoring."""
    if account_kind == "bot":
        if not isinstance(bot_token, str) or not bot_token:
            raise ValueError("Telegram bot Account is missing its token")
        return {"@type": "checkAuthenticationBotToken", "token": bot_token}
    if authorization_mode == "qr":
        return {"@type": "requestQrCodeAuthentication", "other_user_ids": []}
    return None


def challenge_request(kind: str, answer: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a caller-supplied answer. The returned dictionary contains secrets."""
    if not isinstance(answer, Mapping):
        raise ValueError("Telegram authorization answer must be an object")
    if kind == "phone_number":
        return {
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": _required_answer(answer, "phone_number"),
            "settings": None,
        }
    if kind == "code":
        return {"@type": "checkAuthenticationCode", "code": _required_answer(answer, "code")}
    if kind == "password":
        return {
            "@type": "checkAuthenticationPassword",
            "password": _required_answer(answer, "password"),
        }
    if kind == "email_address":
        return {
            "@type": "setAuthenticationEmailAddress",
            "email_address": _required_answer(answer, "email_address"),
        }
    if kind == "email_code":
        return {
            "@type": "checkAuthenticationEmailCode",
            "code": {
                "@type": "emailAddressAuthenticationCode",
                "code": _required_answer(answer, "code"),
            },
        }
    if kind == "registration":
        return {
            "@type": "registerUser",
            "first_name": _required_answer(answer, "first_name"),
            "last_name": _optional_answer(answer, "last_name"),
        }
    raise ValueError("Telegram authorization challenge cannot accept an answer")


def parse_authorization_state(raw: Mapping[str, Any]) -> AuthorizationState:
    """Classify TDLib state without selecting readiness, expiry or disclosure policy."""
    state_type = raw.get("@type")
    if not isinstance(state_type, str):
        return AuthorizationState(category="invalid", state_type="invalid")
    if state_type == "authorizationStateReady":
        return AuthorizationState(category="ready", state_type=state_type)
    if state_type in {"authorizationStateClosing", "authorizationStateClosed"}:
        return AuthorizationState(category="closed", state_type=state_type)
    kind = _STATE_CHALLENGES.get(state_type)
    if kind is None:
        return AuthorizationState(category="unsupported", state_type=state_type)
    metadata: dict[str, Any] = {}
    for value in (raw.get("code_info"), raw.get("email_address_authentication")):
        if not isinstance(value, Mapping):
            continue
        timeout = value.get("timeout")
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 0:
            metadata["timeout_seconds"] = timeout
        delivery = value.get("type")
        if isinstance(delivery, Mapping) and isinstance(delivery.get("@type"), str):
            metadata["delivery_type"] = delivery["@type"]
    for key in ("allow_apple_id", "allow_google_id"):
        if isinstance(raw.get(key), bool):
            metadata[key] = raw[key]
    qr_link = raw.get("link") if kind == "qr" else None
    return AuthorizationState(
        category="challenge",
        state_type=state_type,
        challenge_kind=kind,
        challenge_fields=CHALLENGE_FIELDS[kind],
        metadata=MappingProxyType(sanitize_challenge_metadata(metadata)),
        qr_link=qr_link if isinstance(qr_link, str) and qr_link.startswith("tg://") else None,
    )


def sanitize_challenge_metadata(value: Any) -> dict[str, Any]:
    """Return the same safe provider facts for a parsed or restored challenge."""
    if not isinstance(value, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    timeout = value.get("timeout_seconds")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 0:
        metadata["timeout_seconds"] = timeout
    delivery_type = value.get("delivery_type")
    if isinstance(delivery_type, str) and delivery_type in {
        "authenticationCodeTypeTelegramMessage",
        "authenticationCodeTypeSms",
        "authenticationCodeTypeCall",
        "authenticationCodeTypeFlashCall",
        "authenticationCodeTypeMissedCall",
    }:
        metadata["delivery_type"] = delivery_type
    for key in ("allow_apple_id", "allow_google_id"):
        if isinstance(value.get(key), bool):
            metadata[key] = value[key]
    return metadata


def is_saved_authorization_rejected(error: BaseException) -> bool:
    """Classify explicit provider rejection; the caller decides how to handle it."""
    return (
        isinstance(error, TelegramTdlibRequestError) and error.error_name in _SAVED_AUTH_REJECTIONS
    )


def _required_answer(answer: Mapping[str, Any], key: str) -> str:
    value = answer.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Telegram authorization answer requires {key}")
    return value


def _optional_answer(answer: Mapping[str, Any], key: str) -> str:
    value = answer.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"Telegram authorization answer {key} must be text")
    return value
