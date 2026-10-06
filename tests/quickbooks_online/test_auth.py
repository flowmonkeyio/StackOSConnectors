from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from stackos_connectors import (
    ConnectorAuth,
    OAuthTokenError,
    build_authorization_request,
    get_auth_contract,
    request_token,
)

PROVIDER = "quickbooks-online"
APP = ConnectorAuth("oauth2_authorization_code", {"client_id": "app", "client_secret": "secret"})


def test_declared_accounting_consent_and_explicit_grants():
    contract = get_auth_contract(PROVIDER, method=APP.method)
    assert contract.grant_types == ("authorization_code", "refresh_token")
    assert contract.client_auth_style == "basic"
    assert contract.pkce_mode == "unavailable"
    request = build_authorization_request(
        PROVIDER, auth=APP, redirect_uri="https://consumer.example/callback", state="state-canary"
    )
    parsed = urlparse(request.url)
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://appcenter.intuit.com/connect/oauth2"
    )
    assert parse_qs(parsed.query) == {
        "client_id": ["app"],
        "redirect_uri": ["https://consumer.example/callback"],
        "state": ["state-canary"],
        "response_type": ["code"],
        "scope": ["com.intuit.quickbooks.accounting"],
    }
    assert "state-canary" not in repr(request)
    assert get_auth_contract(PROVIDER, method="oauth2_token").grant_types == ("refresh_token",)


@pytest.mark.parametrize("grant", ["authorization_code", "refresh_token"])
async def test_explicit_grants_use_fixed_basic_endpoint_once(grant):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Basic YXBwOnNlY3JldA=="
        form = parse_qs(request.content.decode())
        assert "client_secret" not in form
        expected = {"grant_type": [grant]}
        if grant == "authorization_code":
            expected.update(
                code=["code-canary"], redirect_uri=["https://consumer.example/callback"]
            )
        else:
            expected["refresh_token"] = ["old-canary"]
        assert form == expected
        return httpx.Response(
            200,
            json={
                "access_token": "new-canary",
                "refresh_token": "rotated-canary",
                "expires_in": 3600,
                "token_type": "bearer",
                "x_refresh_token_expires_in": 86400,
            },
        )

    kwargs = (
        {"code": "code-canary", "redirect_uri": "https://consumer.example/callback"}
        if grant == "authorization_code"
        else {"refresh_token": "old-canary"}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await request_token(PROVIDER, auth=APP, grant_type=grant, http=http, **kwargs)
        assert not http.is_closed
    assert len(calls) == 1
    assert result.access_token == "new-canary" and result.refresh_token == "rotated-canary"
    assert result.expires_in == 3600
    assert result.account_id is None and not result.scopes_present
    assert "canary" not in repr(result)


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_refresh_failure_is_safe_and_never_replayed(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "private-provider-body secret old-canary"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                PROVIDER,
                auth=APP,
                grant_type="refresh_token",
                refresh_token="old-canary",
                http=http,
            )
        assert not http.is_closed
    assert len(calls) == 1
    assert "private-provider-body" not in str(caught.value.__dict__)
    assert "old-canary" not in str(caught.value.__dict__)
    assert "secret" not in str(caught.value.__dict__)
