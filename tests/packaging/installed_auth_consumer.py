"""Run with Python -I outside a checkout against an installed connector wheel."""

import asyncio
import base64
import hashlib
import hmac
import importlib.metadata
import importlib.util
import json
import socket
import sys
import sysconfig
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class LiveNetworkForbidden(BaseException):
    pass


def no_network(*args, **kwargs):
    raise LiveNetworkForbidden("The standalone driver permits injected transports only")


socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.getaddrinfo = no_network
assert importlib.util.find_spec("stackos") is None, "standalone environment contains StackOS"

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from stackos_connectors import (  # noqa: E402
    CallOptions,
    ConnectorAuth,
    OAuthTokenError,
    build_authorization_request,
    get_auth_contract,
    get_default_client,
    probe_credentials,
    project_probe_config,
    request_token,
)
from stackos_connectors.connectors.hubspot.signature import verify_signature_v3  # noqa: E402
from stackos_connectors.connectors.slack_bot.auth import verify_signature_v0  # noqa: E402
from stackos_connectors.connectors.telegram.auth import (  # noqa: E402
    challenge_request,
    initial_request,
    parse_authorization_state,
    sanitize_challenge_metadata,
)

SECRET = "driver-secret-canary"
ACCESS = "driver-access-canary"


async def quickbooks_reads(client):
    """Exercise the installed QBO action factory through the public API."""
    description = client.describe("quickbooks-online")
    assert len(description["actions"]) == 2
    for method in description["auth_methods"]:
        assert method["setup"]["label"]
        setup_fields = {field["key"]: field for field in method["setup"]["fields"]}
        assert setup_fields["environment"]["required"] and setup_fields["realm_id"]["required"]
    raw = '{ "Id":"7", "TxnDate":"2026-01-31", "TotalAmt":9007199254740993.0100, "Balance":-0.001 }'
    calls = []

    def edge(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "sandbox-quickbooks.api.intuit.com"
        assert request.headers["authorization"] == f"Bearer {ACCESS}"
        if request.url.path.endswith("/companyinfo/123456789"):
            return httpx.Response(
                200, json={"CompanyInfo": {"Id": "1", "CompanyName": "Synthetic"}}
            )
        assert request.url.path == "/v3/company/123456789/query"
        assert (
            request.url.params["query"]
            == "select * from Invoice where TxnDate >= '2026-01-01' and TxnDate <= '2026-01-31' "
            "STARTPOSITION 1 MAXRESULTS 10"
        )
        return httpx.Response(
            200,
            content='{"QueryResponse":{"Invoice":[' + raw + '],"startPosition":1,"maxResults":1}}',
        )

    auth = ConnectorAuth(
        "oauth2_token",
        {"access_token": ACCESS},
        config={"environment": "sandbox", "realm_id": "123456789"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(edge)) as http:
        options = CallOptions(http=http, timeout=1)
        company = await client.execute(
            "quickbooks-online", "quickbooks-online.company-info.get", {}, auth, options
        )
        assert company.output_json == {
            "realm_id": "123456789",
            "company_id": "1",
            "company_name": "Synthetic",
        }
        page = await client.execute(
            "quickbooks-online",
            "quickbooks-online.invoices.list",
            {
                "txn_date_from": "2026-01-01",
                "txn_date_to": "2026-01-31",
                "start_position": 1,
                "max_results": 10,
            },
            auth,
            options,
        )
        assert page.output_json["invoices"] == [{"native_id": "7", "raw_json": raw}]
        assert page.output_json["count"] == 1
        probe = await probe_credentials("quickbooks-online", auth=auth, options=options)
        assert probe["ok"]
        assert probe["metadata"]["evidence"] == {
            "account": {
                "provider_account_id": None,
                "display_name": "Synthetic",
                "metadata": {
                    "company_id": "1",
                    "configured_realm_id": "123456789",
                    "environment": "sandbox",
                },
            }
        }
        assert not http.is_closed
    assert len(calls) == 3


async def main():
    before = set(Path.cwd().iterdir())
    client = get_default_client()
    root = resources.files("stackos_connectors")
    providers = {
        key: metadata
        for key, metadata in client.registry.connector_metadata.items()
        if "auth_protocol" in metadata
    }
    assert len(providers) == 17 and "google-indexing" in providers
    methods = 0
    for key, metadata in providers.items():
        assert root.joinpath("connectors", key.replace("-", "_"), "docs", "auth.md").is_file()
        for method in metadata["auth_methods"]:
            if "protocol" in method:
                assert get_auth_contract(key, method=method["key"]).provider_key == key
                methods += 1
    auth = ConnectorAuth(
        "oauth2_authorization_code", {"client_id": "driver", "client_secret": SECRET}
    )
    consent = build_authorization_request(
        "linear",
        auth=auth,
        redirect_uri="https://consumer.example/callback",
        state="driver-state-canary",
        code_challenge="driver-challenge",
    )
    query = parse_qs(urlparse(consent.url).query)
    assert query["state"] == ["driver-state-canary"]
    assert query["code_challenge_method"] == ["S256"]
    assert "driver-state-canary" not in repr(consent)

    grant_calls = []
    for provider in providers:
        if provider == "google-indexing":
            continue  # Its only grant is exercised with a generated JSON key below.
        application_only = provider in {"reddit", "taboola"}
        grant = "client_credentials" if application_only else "authorization_code"
        method = "client_credentials" if application_only else "oauth2_authorization_code"

        def token_edge(request, provider=provider, grant=grant):
            grant_calls.append((provider, request.method))
            if request.method == "POST":
                assert parse_qs(request.content.decode())["grant_type"] == [grant]
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": "driver-refresh-canary",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "read write",
                    "hub_id": 12,
                    "id": "driver-account",
                    "api_domain": "https://fixture.pipedrive.com",
                    "instance_url": "https://fixture.salesforce.com",
                },
            )

        kwargs = (
            {}
            if application_only
            else {
                "code": "driver-code",
                "redirect_uri": "https://consumer.example/callback",
            }
        )
        if provider in {"linear", "salesforce", "microsoft-365"}:
            kwargs["code_verifier"] = "driver-verifier"
        async with httpx.AsyncClient(transport=httpx.MockTransport(token_edge)) as http:
            result = await request_token(
                provider,
                auth=ConnectorAuth(
                    method,
                    {
                        "client_id": "driver",
                        "client_secret": SECRET,
                        "user_agent": "driver:v1",
                    },
                ),
                grant_type=grant,
                http=http,
                **kwargs,
            )
            assert not http.is_closed
        assert result.access_token == ACCESS and result.scopes_present
        assert ACCESS not in repr(result) and SECRET not in repr(auth)
    assert len(grant_calls) == 17  # Meta explicitly performs its second exchange.

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        )
    ) as http:
        renewed = await request_token(
            "google-search-console",
            auth=auth,
            grant_type="refresh_token",
            refresh_token="driver-refresh-canary",
            http=http,
        )
    assert renewed.refresh_token is None and renewed.scopes is None and not renewed.scopes_present

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_json = json.dumps(
        {
            "type": "service_account",
            "client_email": "driver@fixture.iam.gserviceaccount.com",
            "private_key_id": "fixture",
            "token_uri": "https://oauth2.googleapis.com/token",
            "private_key": private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        }
    )

    def jwt_edge(request):
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        encoded = body["assertion"][0].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        assert claims["sub"] == "delegate@example.com"
        assert claims["aud"] == "https://oauth2.googleapis.com/token"
        return httpx.Response(
            200, json={"access_token": ACCESS, "expires_in": 3600, "token_type": "Bearer"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(jwt_edge)) as http:
        signed = await request_token(
            "google-workspace",
            auth=ConnectorAuth(
                "service-account",
                {"service_account_json": key_json},
                {"delegated_subject": "delegate@example.com"},
            ),
            grant_type="jwt_bearer",
            http=http,
        )
    assert signed.access_token == ACCESS and not signed.scopes_present

    def indexing_jwt_edge(request):
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        encoded = body["assertion"][0].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        assert claims["scope"] == "https://www.googleapis.com/auth/indexing"
        assert claims["aud"] == "https://oauth2.googleapis.com/token"
        assert "sub" not in claims
        return httpx.Response(
            200, json={"access_token": ACCESS, "expires_in": 3600, "token_type": "Bearer"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(indexing_jwt_edge)) as http:
        indexing = await request_token(
            "google-indexing",
            auth=ConnectorAuth("service-account", {"service_account_json": key_json}),
            grant_type="jwt_bearer",
            http=http,
        )
        assert not http.is_closed
    assert indexing.access_token == ACCESS and not indexing.scopes_present
    assert indexing.token_type == "Bearer" and indexing.expires_in == 3600
    try:
        await request_token(
            "taboola",
            auth=ConnectorAuth("client_credentials", {}),
            grant_type="refresh_token",
            refresh_token=SECRET,
        )
    except OAuthTokenError as exc:
        assert SECRET not in str(exc)
    else:
        raise AssertionError("unsupported grant was accepted")

    # Explicit grants above are complete. Discovery, probes and actions cannot
    # invoke another grant or make an un-injected provider request below.
    import stackos_connectors.auth as auth_api
    import stackos_connectors.shared.oauth as oauth_protocol

    async def implicit_grant(*args, **kwargs):
        raise AssertionError("implicit grant request")

    auth_api.request_token = implicit_grant
    oauth_protocol.request_token = implicit_grant
    await quickbooks_reads(client)
    client.describe("google-workspace")
    config = project_probe_config(
        "wordpress", {"site_url": "https://fixture.test", "account_ref": "private"}
    )
    assert config == {"wp_url": "https://fixture.test"}
    limited = await probe_credentials(
        "google-workspace", auth=ConnectorAuth("service-account", {"access_token": ACCESS})
    )
    assert limited["metadata"]["resource_access"] == "unverified"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"success": True, "data": {"id": 2, "company_id": 3}}
            )
        )
    ) as http:
        probe = await probe_credentials(
            "pipedrive",
            auth=ConnectorAuth(
                "api_token", {"api_token": SECRET}, {"company_domain": "fixture.pipedrive.com"}
            ),
            options=CallOptions(http=http),
        )
        assert not http.is_closed
    assert probe["metadata"]["evidence"]["account"]["provider_account_id"] == "3"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"organic": [], "echo": SECRET})
        )
    ) as http:
        action = await client.execute(
            "serper",
            "serper.search",
            {"query": "fixture"},
            ConnectorAuth("api_key", {"api_key": SECRET}),
            CallOptions(http=http),
        )
    assert SECRET not in json.dumps(dict(action.output_json))

    assert initial_request("user", "qr", None)["@type"] == "requestQrCodeAuthentication"
    assert (
        challenge_request("code", {"code": "synthetic-code"})["@type"] == "checkAuthenticationCode"
    )
    state = parse_authorization_state({"@type": "authorizationStateWaitCode"})
    assert state.challenge_kind == "code" and state.challenge_fields == ("code",)
    assert sanitize_challenge_metadata({"timeout_seconds": 30, "credential_ref": "private"}) == {
        "timeout_seconds": 30
    }

    body, timestamp, uri = b'{"synthetic":true}', "1700000000", "https://consumer.example/hooks"
    slack = (
        "v0="
        + hmac.new(
            SECRET.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    assert verify_signature_v0(SECRET, timestamp, body, slack)
    assert not verify_signature_v0(SECRET, timestamp, body + b" ", slack)
    source = b"POST" + uri.encode() + body + timestamp.encode()
    hubspot = base64.b64encode(hmac.new(SECRET.encode(), source, hashlib.sha256).digest()).decode()
    assert verify_signature_v3(SECRET, "POST", uri, timestamp, body, hubspot)
    assert not verify_signature_v3(SECRET, "POST", uri, timestamp, body + b" ", hubspot)
    assert set(Path.cwd().iterdir()) == before
    origins = {
        name: module.__file__
        for name, module in sys.modules.items()
        if name.startswith("stackos_connectors") and getattr(module, "__file__", None)
    }
    installed = Path(sysconfig.get_paths()["purelib"]).resolve()
    assert all(Path(path).resolve().is_relative_to(installed) for path in origins.values())
    assert "stackos" not in sys.modules
    print(
        json.dumps(
            {
                "status": "passed",
                "version": importlib.metadata.version("stackos-connectors"),
                "protocol_providers": len(providers),
                "protocol_methods": methods,
                "explicit_code_or_client_requests": len(grant_calls),
                "categories": [
                    "discovery",
                    "authorization",
                    "code",
                    "refresh",
                    "client_credentials",
                    "delegated_jwt",
                    "indexing_jwt",
                    "probe",
                    "action",
                    "telegram",
                    "slack_v0",
                    "hubspot_v3",
                    "quickbooks_exact_reads",
                    "quickbooks_company_probe",
                ],
                "network": "blocked; injected edges only",
                "origins": origins,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
