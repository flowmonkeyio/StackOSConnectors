import base64
import json
import traceback

import httpx
import pytest

import stackos_connectors
from stackos_connectors import ConnectorAuth, OAuthTokenError, get_auth_contract

REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
APP_SECRET = "revocation-app-secret-canary"
LATEST_TOKEN = "latest-refresh-token-canary"


def application(method="oauth2_authorization_code", environment="sandbox", fields=None):
    return ConnectorAuth(
        method,
        fields if fields is not None else {"client_id": "app", "client_secret": APP_SECRET},
        {
            "environment": environment,
            "realm_id": "123456789",
            "revocation_endpoint": "https://untrusted.example/revoke",
        },
    )


class UnreadResponse(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        raise AssertionError("revocation must not read or decode provider response bodies")
        yield b""  # pragma: no cover

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("environment", ["sandbox", "production"])
@pytest.mark.parametrize("method", ["oauth2_authorization_code", "oauth2_token"])
async def test_public_revoke_sends_one_exact_request(method, environment):
    requests = []
    stream = UnreadResponse()

    def edge(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == REVOKE_URL
        assert request.headers["content-type"] == "application/json"
        expected = base64.b64encode(f"app:{APP_SECRET}".encode()).decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        assert json.loads(request.content) == {"token": LATEST_TOKEN}
        assert set(request.extensions["timeout"].values()) == {0.75}
        return httpx.Response(200, stream=stream)

    auth = application(method, environment)
    async with httpx.AsyncClient(transport=httpx.MockTransport(edge)) as http:
        result = await stackos_connectors.revoke_token(
            "quickbooks-online", auth=auth, token=LATEST_TOKEN, http=http, timeout=0.75
        )
        assert result is None
        assert not http.is_closed
    assert len(requests) == 1
    assert stream.closed
    assert (
        get_auth_contract(
            "quickbooks-online", method=method, config=auth.config
        ).revocation_endpoint
        == REVOKE_URL
    )


@pytest.mark.parametrize("status", [201, 204, 301, 307, 400, 401, 408, 429, 500, 503])
async def test_non200_never_confirms_or_follows_redirects(status):
    requests = []
    stream = UnreadResponse()

    def edge(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://untrusted.example/revoke", "Content-Length": "999999999"},
            stream=stream,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(edge), follow_redirects=True
    ) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await stackos_connectors.revoke_token(
                "quickbooks-online", auth=application(), token=LATEST_TOKEN, http=http
            )
        assert not http.is_closed
    assert len(requests) == 1
    assert stream.closed
    assert caught.value.category == "provider_rejected"
    assert caught.value.status_code == status
    assert APP_SECRET not in str(caught.value) and LATEST_TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "provider,method,fields,token,timeout",
    [
        ("linear", "oauth2_authorization_code", None, LATEST_TOKEN, 1),
        ("missing", "oauth2_authorization_code", None, LATEST_TOKEN, 1),
        ("quickbooks-online", "missing", None, LATEST_TOKEN, 1),
        ("quickbooks-online", "oauth2_token", {}, LATEST_TOKEN, 1),
        ("quickbooks-online", "oauth2_token", {"client_id": "app"}, LATEST_TOKEN, 1),
        ("quickbooks-online", "oauth2_token", {"client_secret": APP_SECRET}, LATEST_TOKEN, 1),
        (
            "quickbooks-online",
            "oauth2_token",
            {"client_id": " ", "client_secret": "s"},
            LATEST_TOKEN,
            1,
        ),
        ("quickbooks-online", "oauth2_token", None, "", 1),
        ("quickbooks-online", "oauth2_token", None, " ", 1),
        ("quickbooks-online", "oauth2_token", None, None, 1),
        ("quickbooks-online", "oauth2_token", None, 3, 1),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, 0),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, -1),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, float("inf")),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, float("nan")),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, True),
        ("quickbooks-online", "oauth2_token", None, LATEST_TOKEN, "1"),
    ],
)
async def test_invalid_or_unsupported_input_rejects_before_dispatch(
    provider, method, fields, token, timeout
):
    def forbidden(request):
        pytest.fail("invalid revocation input reached the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await stackos_connectors.revoke_token(
                provider,
                auth=application(method=method, fields=fields),
                token=token,
                http=http,
                timeout=timeout,
            )
    assert caught.value.category == "invalid_input"
    assert APP_SECRET not in str(caught.value) and LATEST_TOKEN not in str(caught.value)


async def test_transport_failure_is_safe_and_never_retried():
    requests = []

    def edge(request):
        requests.append(request)
        raise httpx.ReadTimeout(f"{APP_SECRET} {LATEST_TOKEN}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(edge)) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await stackos_connectors.revoke_token(
                "quickbooks-online", auth=application(), token=LATEST_TOKEN, http=http
            )
        assert not http.is_closed
    assert len(requests) == 1
    assert caught.value.category == "transport"
    assert caught.value.status_code is None
    diagnostic = "".join(traceback.format_exception(caught.value))
    assert APP_SECRET not in diagnostic and LATEST_TOKEN not in diagnostic
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("status", [200, 400])
async def test_owned_http_client_closes_after_success_or_rejection(monkeypatch, status):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: http)
    try:
        await stackos_connectors.revoke_token(
            "quickbooks-online", auth=application(), token=LATEST_TOKEN
        )
    except OAuthTokenError:
        assert status == 400
    else:
        assert status == 200
    assert http.is_closed
