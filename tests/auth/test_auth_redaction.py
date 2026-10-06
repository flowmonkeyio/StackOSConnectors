import httpx
import pytest

from stackos_connectors import ConnectorAuth
from stackos_connectors.auth import OAuthTokenError, TokenResult, request_token


def test_sensitive_result_is_explicit_not_repr():
    result = TokenResult(
        access_token="SECRET-ACCESS",
        refresh_token="SECRET-REFRESH",
        metadata={"account": "SECRET-META"},
        config_updates={"base_url": "SECRET-URL"},
    )
    assert "SECRET" not in repr(result)
    assert result.access_token == "SECRET-ACCESS"
    with pytest.raises(TypeError):
        result.metadata["mutate"] = True


async def test_transport_exception_is_not_chained_with_credentials():
    def handler(request):
        raise httpx.ConnectError("SECRET-TRANSPORT", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OAuthTokenError) as caught:
            await request_token(
                "taboola",
                auth=ConnectorAuth(
                    "client_credentials", {"client_id": "id", "client_secret": "SECRET-CLIENT"}
                ),
                grant_type="client_credentials",
                http=http,
            )
    assert "SECRET" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert caught.value.retryable
