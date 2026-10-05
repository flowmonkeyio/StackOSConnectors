import json

import httpx
import pytest

from stackos_connectors.connectors.pipedrive.integration import PipedriveIntegration
from stackos_connectors.connectors.salesloft.integration import SalesloftIntegration
from stackos_connectors.probe import AuthMethodProbeContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,method,field",
    [
        ("pipedrive", "api_token", "api_token"),
        ("pipedrive", "oauth2_authorization_code", "access_token"),
        ("pipedrive", "oauth2_token", "access_token"),
        ("salesloft", "api_key", "api_key"),
        ("salesloft", "oauth2_authorization_code", "access_token"),
        ("salesloft", "oauth2_token", "access_token"),
    ],
)
async def test_resolved_token_probe_needs_method_but_no_host_policy(provider, method, field):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET" and request.content == b""
        if provider == "pipedrive":
            assert str(request.url) == "https://acme.pipedrive.com/api/v1/users/me"
            if method == "api_token":
                assert request.headers["x-api-token"] == "synthetic-token"
                assert "authorization" not in request.headers
            else:
                assert request.headers["authorization"] == "Bearer synthetic-token"
                assert "x-api-token" not in request.headers
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"id": 123, "company_id": 456, "company_name": "Example"},
                },
            )
        assert str(request.url) == "https://api.salesloft.com/v2/me"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"id": 123, "guid": "native-user", "name": "Example"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cls = PipedriveIntegration if provider == "pipedrive" else SalesloftIntegration
        kwargs = {"api_domain": "acme.pipedrive.com"} if provider == "pipedrive" else {}
        probe = cls(
            payload=json.dumps({field: "synthetic-token"}).encode(),
            http=http,
            probe_context=AuthMethodProbeContext(auth_method_key=method),
            **kwargs,
        )
        result = await probe.test_credentials()
        assert not http.is_closed
    assert result["ok"] is True
    assert len(seen) == 1
    assert "grants" not in result["metadata"]["evidence"]
    assert result["metadata"]["evidence"]["account"]["provider_account_id"] == (
        "456" if provider == "pipedrive" else "native-user"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,kwargs",
    [(PipedriveIntegration, {"api_domain": "acme.pipedrive.com"}), (SalesloftIntegration, {})],
)
async def test_unknown_method_remains_zero_send(cls, kwargs):
    def handle(_):
        raise AssertionError("unsupported method must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        probe = cls(
            payload=b'{"access_token":"synthetic"}',
            http=http,
            probe_context=AuthMethodProbeContext(auth_method_key="unknown"),
            **kwargs,
        )
        result = await probe.test_credentials()
    assert result["ok"] is False and result["status"] == "unsupported_auth_method"
