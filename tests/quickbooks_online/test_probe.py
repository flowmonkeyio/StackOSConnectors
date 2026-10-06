import json
import socket

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client, probe_credentials
from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError
from stackos_connectors.probe import AuthProbeEvidence, project_probe_config

PROVIDER = "quickbooks-online"
TOKEN = "qbo-probe-access-canary"
CONFIG = {"environment": "sandbox", "realm_id": "123456789"}


@pytest.fixture(autouse=True)
def no_implicit_grant_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("probe must use the supplied token and injected HTTP transport")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr("stackos_connectors.auth.request_token", forbidden)
    monkeypatch.setattr("stackos_connectors.shared.oauth.request_token", forbidden)


@pytest.mark.parametrize("method", ["oauth2_authorization_code", "oauth2_token"])
def test_setup_declares_required_explicit_company_context(method):
    description = get_default_client().describe(PROVIDER)
    declaration = next(item for item in description["auth_methods"] if item["key"] == method)
    setup = declaration["setup"]
    assert setup["label"]
    fields = {field["key"]: field for field in setup["fields"]}
    environment, realm = fields["environment"], fields["realm_id"]
    assert environment["type"] == "select" and environment["required"]
    assert not environment["secret"]
    assert {option["value"] for option in environment["options"]} == {"sandbox", "production"}
    assert realm["type"] == "text" and realm["required"] and not realm["secret"]
    assert "numeric" in realm["description"].lower()
    schema = declaration["config_schema"]
    assert schema["required"] == ["environment", "realm_id"]
    assert schema["properties"]["realm_id"]["pattern"] == "^[0-9]{1,32}$"
    assert "default" not in schema["properties"]["environment"]
    assert "default" not in environment and not setup.get("config")
    assert project_probe_config(PROVIDER, {**CONFIG, "account_ref": "private-host-ref"}) == CONFIG


@pytest.mark.parametrize("method", ["oauth2_authorization_code", "oauth2_token"])
@pytest.mark.parametrize(
    "environment,host",
    [("sandbox", "sandbox-quickbooks.api.intuit.com"), ("production", "quickbooks.api.intuit.com")],
)
async def test_probe_uses_company_read_and_preserves_distinct_identity(method, environment, host):
    calls = []

    class Limiter:
        count = 0

        async def acquire(self, n=1):
            self.count += 1

    limiter = Limiter()

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert str(request.url) == f"https://{host}/v3/company/123456789/companyinfo/123456789"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.extensions["timeout"]["read"] == 0.5
        return httpx.Response(
            200,
            json={"CompanyInfo": {"Id": "1", "CompanyName": "Example", "Email": "private"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await probe_credentials(
            PROVIDER,
            auth=ConnectorAuth(
                method, {"access_token": TOKEN}, {**CONFIG, "environment": environment}
            ),
            options=CallOptions(http=http, timeout=0.5, rate_limiter=limiter),
        )
        assert not http.is_closed
    assert result["ok"] and result["vendor"] == PROVIDER and result["status"] == "ok"
    evidence = result["metadata"]["evidence"]
    assert evidence == {
        "account": {
            "provider_account_id": None,
            "display_name": "Example",
            "metadata": {
                "company_id": "1",
                "configured_realm_id": "123456789",
                "environment": environment,
            },
        }
    }
    assert AuthProbeEvidence.model_validate(evidence).grants is None
    assert TOKEN not in json.dumps(result) and "private" not in json.dumps(result)
    assert len(calls) == 1 and limiter.count == 1


async def test_probe_optional_company_name_does_not_gain_scope_evidence():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"CompanyInfo": {"Id": "1"}})
        )
    ) as http:
        result = await probe_credentials(
            PROVIDER,
            auth=ConnectorAuth("oauth2_token", {"access_token": TOKEN}, CONFIG),
            options=CallOptions(http=http),
        )
    evidence = result["metadata"]["evidence"]
    assert evidence["account"]["display_name"] is None
    assert "grants" not in evidence and "scope" not in json.dumps(result)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"realm_id": "123"},
        {"environment": "sandbox"},
        {**CONFIG, "environment": "unknown"},
        {**CONFIG, "realm_id": "../123"},
        {**CONFIG, "realm_id": 123},
    ],
)
async def test_probe_rejects_missing_or_invalid_context_before_io(config):
    with pytest.raises(ValidationError, match="inputs are invalid"):
        await probe_credentials(
            PROVIDER, auth=ConnectorAuth("oauth2_token", {"access_token": TOKEN}, config)
        )


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"CompanyInfo":{"Id":1}}',
        b'{"CompanyInfo":{"Id":"1","Id":"2"}}',
        b'{"CompanyInfo":{"Id":"1","CompanyName":""}}',
        json.dumps({"CompanyInfo": {"Id": "1", "CompanyName": TOKEN}}).encode(),
        json.dumps({"CompanyInfo": {"Id": "1", "private": TOKEN}}).encode(),
    ],
)
async def test_probe_reuses_malformed_and_credential_echo_rejection(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        with pytest.raises(IntegrationDownError) as caught:
            await probe_credentials(
                PROVIDER,
                auth=ConnectorAuth("oauth2_token", {"access_token": TOKEN}, CONFIG),
                options=CallOptions(http=http),
            )
    assert caught.value.data == {"stage": "test", "reason_code": "invalid_response"}
    assert TOKEN not in str(caught.value.__dict__)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
    ],
)
async def test_probe_preserves_safe_provider_error_without_body(status, reason):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                status,
                json={"Fault": {"Error": [{"Message": f"private-body {TOKEN}"}]}},
                headers={"Retry-After": "0.001"},
            )
        )
    ) as http:
        with pytest.raises(IntegrationDownError) as caught:
            await probe_credentials(
                PROVIDER,
                auth=ConnectorAuth("oauth2_token", {"access_token": TOKEN}, CONFIG),
                options=CallOptions(http=http),
            )
    assert caught.value.data == {"stage": "test", "status": status, "reason_code": reason}
    assert TOKEN not in str(caught.value.__dict__) and "private-body" not in str(
        caught.value.__dict__
    )


async def test_probe_never_returns_unreviewed_exception_details(monkeypatch):
    async def fail(*args, **kwargs):
        raise ConnectorError(
            f"unsafe-detail {TOKEN}",
            provider_error={"reason_code": "unsafe-reason", "body": f"unsafe-body {TOKEN}"},
        )

    monkeypatch.setattr(get_default_client(), "execute", fail)
    with pytest.raises(IntegrationDownError) as caught:
        await probe_credentials(
            PROVIDER, auth=ConnectorAuth("oauth2_token", {"access_token": TOKEN}, CONFIG)
        )
    assert caught.value.data == {"stage": "test", "reason_code": "probe_error"}
    assert "unsafe" not in str(caught.value.__dict__) and TOKEN not in str(caught.value.__dict__)
