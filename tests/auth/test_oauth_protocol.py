from urllib.parse import parse_qs

import httpx
import pytest

from stackos_connectors import ConnectorAuth
from stackos_connectors.auth import OAuthTokenError, request_token

PROVIDERS = (
    "google-ads",
    "google-workspace",
    "google-search-console",
    "google-analytics",
    "google-tag-manager",
    "meta-ads",
    "salesforce",
    "pipedrive",
    "outreach",
    "salesloft",
    "microsoft-365",
    "hubspot",
    "linear",
    "taboola",
    "reddit",
)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("refresh", [False, True])
async def test_existing_provider_grant_matrix(provider, refresh):
    client_credentials = provider in {"reddit", "taboola"}
    if refresh and client_credentials:
        return
    method = "client_credentials" if client_credentials else "oauth2_authorization_code"
    grant = (
        "client_credentials"
        if client_credentials
        else "refresh_token"
        if refresh
        else "authorization_code"
    )
    seen = []

    def handler(request):
        seen.append(request)
        body = parse_qs(request.content.decode())
        if request.method == "POST":
            assert body["grant_type"] == [grant]
            if grant == "refresh_token":
                assert body["refresh_token"] == ["previous"]
            if provider in {"reddit", "pipedrive"}:
                assert request.headers["authorization"] == "Basic aWQ6c2VjcmV0"
                assert "client_secret" not in body
            else:
                assert body["client_secret"] == ["secret"]
        return httpx.Response(
            200,
            json={
                "access_token": "new",
                "refresh_token": "rotated",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "read write",
                "hub_id": 123,
                "id": "account",
                "api_domain": "https://test.pipedrive.com",
                "instance_url": "https://test.salesforce.com",
            },
        )

    kwargs = {"refresh_token": "previous"} if refresh else {}
    if grant == "authorization_code":
        kwargs.update(code="code", redirect_uri="https://consumer.example/cb")
        if provider in {"linear", "salesforce", "microsoft-365"}:
            kwargs["code_verifier"] = "verifier"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await request_token(
            provider,
            auth=ConnectorAuth(
                method,
                {
                    "client_id": "id",
                    "client_secret": "secret",
                    "user_agent": "test-agent",
                },
            ),
            grant_type=grant,
            http=http,
            **kwargs,
        )
    assert result.access_token == "new" and result.refresh_token == "rotated"
    assert result.scopes == ("read", "write") and result.scopes_present
    assert len(seen) == (2 if provider == "meta-ads" and not refresh else 1)


@pytest.mark.parametrize(
    "scope,expected",
    [("", ()), ([], ()), (None, None), ("one%3Atwo+three,four", ("one:two", "three", "four"))],
)
async def test_explicit_scope_evidence_is_distinct_from_omission(scope, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "new", "scope": scope})
        )
    ) as http:
        result = await request_token(
            "google-workspace",
            auth=ConnectorAuth("oauth2_token", {"client_id": "id", "client_secret": "secret"}),
            grant_type="refresh_token",
            refresh_token="previous",
            http=http,
        )
    assert result.scopes == expected and result.scopes_present


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grant_type": "authorization_code"},
        {"grant_type": "refresh_token"},
        {"grant_type": "client_credentials", "refresh_token": "unexpected"},
        {"grant_type": "client_credentials", "code": "unexpected"},
        {"grant_type": "client_credentials", "timeout": 0},
    ],
)
async def test_wrong_or_incomplete_grant_fails_before_http(kwargs):
    def forbidden(request):
        pytest.fail("invalid input reached provider HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        with pytest.raises(OAuthTokenError):
            await request_token(
                "taboola",
                auth=ConnectorAuth(
                    "client_credentials", {"client_id": "id", "client_secret": "secret"}
                ),
                http=http,
                **kwargs,
            )


@pytest.mark.parametrize("body", [b"not-json SECRET", b"x" * 1_000_001])
async def test_unparseable_or_oversized_response_is_safe(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                "taboola",
                auth=ConnectorAuth(
                    "client_credentials", {"client_id": "id", "client_secret": "secret"}
                ),
                grant_type="client_credentials",
                http=http,
            )
    assert caught.value.retryable and caught.value.status_code == 200
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example",
        "http://test.pipedrive.com",
        "https://user:secret@test.pipedrive.com",
        "https://test.pipedrive.com/path",
        "https://test.pipedrive.com?secret=1",
    ],
)
async def test_provider_execution_base_remains_trusted(url):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "token", "api_domain": url})
        )
    ) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                "pipedrive",
                auth=ConnectorAuth("oauth2_token", {"client_id": "id", "client_secret": "secret"}),
                grant_type="refresh_token",
                refresh_token="previous",
                http=http,
            )
    assert caught.value.retryable and caught.value.status_code == 200
    assert url not in str(caught.value)


async def test_explicit_client_credentials_and_caller_client_lifetime():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url == "https://backstage.taboola.com/backstage/oauth/token"
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["client_credentials"],
            "client_id": ["id"],
            "client_secret": ["secret"],
        }
        return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await request_token(
            "taboola",
            auth=ConnectorAuth(
                "client_credentials", {"client_id": "id", "client_secret": "secret"}
            ),
            grant_type="client_credentials",
            http=http,
        )
        assert not http.is_closed
    assert result.access_token == "token"
    assert result.refresh_token is None and result.scopes is None and not result.scopes_present
    assert len(seen) == 1


async def test_manual_token_refresh_does_not_apply_execution_schema():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "new"}))
    ) as http:
        result = await request_token(
            "google-workspace",
            auth=ConnectorAuth("oauth2_token", {"client_id": "id", "client_secret": "secret"}),
            grant_type="refresh_token",
            refresh_token="old-refresh",
            http=http,
        )
    assert result.access_token == "new" and result.refresh_token is None
    assert result.scopes is None and not result.scopes_present


@pytest.mark.parametrize(
    "status,retryable",
    [(400, False), (401, False), (408, True), (425, True), (429, True), (500, True), (307, False)],
)
async def test_provider_failures_are_safe_and_classified(status, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                status,
                headers={"Location": "https://attacker.example"},
                json={"error": "invalid_grant", "error_description": "secret-value"},
            )
        ),
        follow_redirects=True,
    ) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                "taboola",
                auth=ConnectorAuth(
                    "client_credentials", {"client_id": "id", "client_secret": "secret-value"}
                ),
                grant_type="client_credentials",
                http=http,
            )
    assert caught.value.retryable is retryable
    assert "secret-value" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"access_token": " "},
        {
            "access_token": "token",
            "refresh_token": "r",
            "expires_in": True,
            "scope": "read write",
            "token_type": "Bearer",
        },
    ],
)
async def test_invalid_required_token_response(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(OAuthTokenError):
            await request_token(
                "linear",
                auth=ConnectorAuth(
                    "oauth2_authorization_code", {"client_id": "id", "client_secret": "s"}
                ),
                grant_type="authorization_code",
                code="c",
                redirect_uri="https://consumer.example/cb",
                code_verifier="v",
                http=http,
            )


async def test_pipedrive_basic_auth_and_trusted_base():
    def handler(request):
        assert request.headers["authorization"] == "Basic aWQ6c2VjcmV0"
        assert "client_secret" not in parse_qs(request.content.decode())
        return httpx.Response(
            200, json={"access_token": "token", "api_domain": "https://example.pipedrive.com/"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await request_token(
            "pipedrive",
            auth=ConnectorAuth(
                "oauth2_authorization_code", {"client_id": "id", "client_secret": "secret"}
            ),
            grant_type="authorization_code",
            code="c",
            redirect_uri="https://consumer.example/cb",
            http=http,
        )
    assert result.config_updates == {"base_url": "https://example.pipedrive.com"}


async def test_meta_exact_two_step_exchange():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"access_token": "short" if len(seen) == 1 else "long"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await request_token(
            "meta-ads",
            auth=ConnectorAuth(
                "oauth2_authorization_code", {"client_id": "id", "client_secret": "secret"}
            ),
            grant_type="authorization_code",
            code="c",
            redirect_uri="https://consumer.example/cb",
            http=http,
        )
    assert [r.method for r in seen] == ["POST", "GET"]
    assert seen[1].url.params["fb_exchange_token"] == "short"
    assert result.access_token == "long"
