import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from stackos_connectors import ConnectorAuth
from stackos_connectors.auth import OAuthTokenError, request_token
from stackos_connectors.shared.google.service_account import (
    sign_assertion,
    validate_service_account,
)


def make_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    value = json.dumps(
        {
            "type": "service_account",
            "client_email": "test@project.iam.gserviceaccount.com",
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "private_key_id": "kid",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    return key, value


def test_signed_assertion_claims_and_signature():
    key, value = make_key()
    assertion = sign_assertion(value=value, scopes=("calendar",), subject="user@example.com")
    head, body, signature = assertion.split(".")

    def decoded(text):
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    key.public_key().verify(
        decoded(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    claims = json.loads(decoded(body))
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    assert claims["sub"] == "user@example.com" and claims["scope"] == "calendar"
    assert claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize(
    "value", [None, "bad", "[]", "x" * 65537, json.dumps({"type": "external_account"})]
)
def test_invalid_key_is_safe(value):
    with pytest.raises(OAuthTokenError):
        validate_service_account(value)


async def test_explicit_jwt_grant():
    _, value = make_key()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"}
            )
        )
    ) as http:
        result = await request_token(
            "google-workspace",
            auth=ConnectorAuth("service-account", {"service_account_json": value}),
            grant_type="jwt_bearer",
            http=http,
        )
    assert result.access_token == "token" and result.scopes is None and not result.scopes_present


@pytest.mark.parametrize(
    "provider",
    [
        "google-ads",
        "google-workspace",
        "google-search-console",
        "google-analytics",
        "google-tag-manager",
    ],
)
async def test_five_released_jwt_providers(provider):
    from urllib.parse import parse_qs

    from stackos_connectors.auth import get_auth_contract

    _, value = make_key()
    expected_scopes = get_auth_contract(provider, method="service-account").scopes

    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/token"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        assert set(body) == {"grant_type", "assertion"}
        payload = body["assertion"][0].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["scope"] == " ".join(expected_scopes)
        assert "sub" not in claims
        return httpx.Response(
            200, json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await request_token(
            provider,
            auth=ConnectorAuth("service-account", {"service_account_json": value}),
            grant_type="jwt_bearer",
            http=http,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"token_uri": "https://attacker.example"},
        {"universe_domain": "attacker.example"},
        {"credential_source": {}},
        {"client_email": "human@example.com"},
        {"private_key": "SECRET-invalid"},
        {"private_key_id": "bad key"},
    ],
)
def test_key_trust_validation_preserved(change):
    _, raw = make_key()
    value = json.loads(raw)
    value.update(change)
    with pytest.raises(OAuthTokenError) as caught:
        validate_service_account(json.dumps(value))
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("scope", ["", [], None, "bad\npermission"])
async def test_present_invalid_jwt_scope_is_not_omission(scope):
    _, value = make_key()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "access_token": "token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": scope,
                },
            )
        )
    ) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                "google-workspace",
                auth=ConnectorAuth("service-account", {"service_account_json": value}),
                grant_type="jwt_bearer",
                http=http,
            )
    assert "scope" in caught.value.invalid_fields
