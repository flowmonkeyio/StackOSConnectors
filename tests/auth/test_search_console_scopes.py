from urllib.parse import parse_qs, urlparse

import pytest

from stackos_connectors import ConnectorAuth, auth, describe

READ = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE = "https://www.googleapis.com/auth/webmasters"


@pytest.mark.parametrize(
    "method",
    ["oauth2_authorization_code", "oauth2_access_token", "oauth2_refresh_token", "service-account"],
)
def test_explicit_write_mode_and_default_readonly(method):
    assert auth.get_auth_contract("google-search-console", method=method).scopes == (READ,)
    assert auth.get_auth_contract(
        "google-search-console", method=method, config={"access_mode": "sitemap_write"}
    ).scopes == (WRITE,)
    with pytest.raises(auth.OAuthTokenError) as caught:
        auth.get_auth_contract(
            "google-search-console", method=method, config={"access_mode": "SECRET-invalid"}
        )
    assert "SECRET-invalid" not in str(caught.value)
    assert caught.value.invalid_fields == ("access_mode",)


def test_catalog_exposes_write_choice_on_each_existing_method():
    for method in describe("google-search-console")["auth_methods"]:
        field = next(f for f in method["setup"]["fields"] if f["key"] == "access_mode")
        assert not field["secret"] and not field["required"]
        assert [option["value"] for option in field["options"]] == ["readonly", "sitemap_write"]
        assert method["config_schema"]["properties"]["access_mode"]["enum"] == [
            "readonly",
            "sitemap_write",
        ]


def test_write_consent_requests_only_explicit_scope():
    request = auth.build_authorization_request(
        "google-search-console",
        auth=ConnectorAuth(
            "oauth2_authorization_code",
            {"client_id": "synthetic-app"},
            config={"access_mode": "sitemap_write"},
        ),
        redirect_uri="https://consumer.example/callback",
        state="synthetic-state",
    )
    assert parse_qs(urlparse(request.url).query)["scope"] == [WRITE]


def test_scope_compatibility_is_one_way_and_provider_specific():
    assert auth.scope_satisfies("google-search-console", READ, {WRITE})
    assert auth.scope_satisfies("google-search-console", WRITE, {WRITE})
    assert not auth.scope_satisfies("google-search-console", WRITE, {READ})
    assert not auth.scope_satisfies("google-analytics", READ, {WRITE})
    assert not auth.scope_satisfies("google-search-console", "invented", {WRITE})
    assert auth.scope_satisfies("google-analytics", "exact", {"exact"})
