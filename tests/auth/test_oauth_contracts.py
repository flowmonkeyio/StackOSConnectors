from urllib.parse import parse_qs, urlparse

import pytest

from stackos_connectors import ConnectorAuth
from stackos_connectors.auth import (
    OAuthTokenError,
    build_authorization_request,
    get_auth_contract,
)

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
def test_released_contracts(provider):
    method = (
        "client_credentials" if provider in {"taboola", "reddit"} else "oauth2_authorization_code"
    )
    contract = get_auth_contract(provider, method=method)
    assert contract.provider_key == provider
    assert contract.token_endpoint.startswith("https://")


def test_authorization_request_preserves_linear_protocol_and_hides_state():
    result = build_authorization_request(
        "linear",
        auth=ConnectorAuth("oauth2_authorization_code", {"client_id": "app"}),
        redirect_uri="https://consumer.example/callback",
        state="secret-state",
        code_challenge="challenge",
    )
    query = parse_qs(urlparse(result.url).query)
    assert query == {
        "client_id": ["app"],
        "redirect_uri": ["https://consumer.example/callback"],
        "state": ["secret-state"],
        "response_type": ["code"],
        "scope": ["read,write"],
        "actor": ["user"],
        "code_challenge": ["challenge"],
        "code_challenge_method": ["S256"],
    }
    assert "secret-state" not in repr(result)


@pytest.mark.parametrize(
    "provider,method",
    [
        ("missing", "oauth2_token"),
        ("pipedrive", "api_token"),
        ("google-indexing", "service-account"),
        ("linear", "missing"),
    ],
)
def test_unsupported_auth_does_not_gain_a_protocol(provider, method):
    with pytest.raises(OAuthTokenError):
        get_auth_contract(provider, method=method)


def test_dynamic_endpoints_and_delegation():
    assert (
        "/organizations/"
        in get_auth_contract(
            "microsoft-365",
            method="oauth2_token",
            config={"tenant": "organizations"},
        ).token_endpoint
    )
    assert (
        get_auth_contract(
            "salesforce", method="oauth2_token", config={"environment": "sandbox"}
        ).token_endpoint
        == "https://test.salesforce.com/services/oauth2/token"
    )
    with pytest.raises(OAuthTokenError):
        get_auth_contract("microsoft-365", method="oauth2_token", config={"tenant": "evil/../host"})
    assert get_auth_contract("google-workspace", method="service-account").scopes == (
        "https://www.googleapis.com/auth/calendar.events",
    )
    with pytest.raises(OAuthTokenError):
        get_auth_contract(
            "google-ads", method="service-account", config={"delegated_subject": "user@example.com"}
        )


def test_pkce_and_optional_scopes_are_provider_declared():
    auth = ConnectorAuth("oauth2_authorization_code", {"client_id": "app"})
    with pytest.raises(OAuthTokenError):
        build_authorization_request(
            "linear", auth=auth, redirect_uri="https://consumer.example/cb", state="state"
        )
    with pytest.raises(OAuthTokenError):
        build_authorization_request(
            "hubspot",
            auth=auth,
            redirect_uri="https://consumer.example/cb",
            state="state",
            optional_scopes=["invented.permission"],
        )


def test_hubspot_declared_optional_bundle_and_metadata_are_passive(monkeypatch):
    import stackos_connectors.auth as auth_module
    from stackos_connectors import describe

    async def forbidden(*args, **kwargs):
        pytest.fail("metadata read invoked token acquisition")

    monkeypatch.setattr(auth_module, "request_token", forbidden)
    contract = get_auth_contract("hubspot", method="oauth2_authorization_code")
    optional = contract.optional_scope_bundles["automation"]["optional_scopes"]
    request = build_authorization_request(
        "hubspot",
        auth=ConnectorAuth("oauth2_authorization_code", {"client_id": "app"}),
        redirect_uri="https://consumer.example/cb",
        state="state",
        optional_scopes=optional,
    )
    assert parse_qs(urlparse(request.url).query)["optional_scope"] == ["automation"]
    metadata = describe("hubspot")
    assert metadata["config"]["scopes"] == list(contract.scopes)
    assert "readiness_group" not in contract.optional_scope_bundles["automation"]
    method = next(m for m in metadata["auth_methods"] if m["key"] == "oauth2_authorization_code")
    keys = {field["key"] for field in method["setup"]["fields"]}
    assert {"client_id", "client_secret", "scope_bundles", "app_id"}.issubset(keys)
    assert not {"field_mapping_ref", "webhook_enabled", "webhook_event_allowlist"} & keys
    assert method["setup"]["interactive"] and method["evidence_source"] == "oauth_response"
    assert "permission_verification" not in method


# Frozen from released host c76238b + provider manifests; no runtime host dependency.
RELEASED_CONTRACTS = {
    "google-ads": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "authorization_params": (
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
        "flow": "authorization_code",
        "pkce_mode": "supported",
        "provider_key": "google-ads",
        "scopes": ("https://www.googleapis.com/auth/adwords",),
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    "google-analytics": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "authorization_params": (
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
        "flow": "authorization_code",
        "pkce_mode": "supported",
        "provider_key": "google-analytics",
        "scopes": ("https://www.googleapis.com/auth/analytics.readonly",),
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    "google-search-console": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "authorization_params": (
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
        "flow": "authorization_code",
        "pkce_mode": "supported",
        "provider_key": "google-search-console",
        "scopes": ("https://www.googleapis.com/auth/webmasters.readonly",),
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    "google-tag-manager": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "authorization_params": (
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
        "flow": "authorization_code",
        "pkce_mode": "supported",
        "provider_key": "google-tag-manager",
        "scopes": ("https://www.googleapis.com/auth/tagmanager.readonly",),
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    "google-workspace": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "authorization_params": (
            ("access_type", "offline"),
            ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
        "flow": "authorization_code",
        "pkce_mode": "supported",
        "provider_key": "google-workspace",
        "scopes": (
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.events",
        ),
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    "hubspot": {
        "authorization_code_response_requirements": (
            "refresh_token",
            "expires_in",
            "scope_evidence",
        ),
        "authorization_endpoint": "https://app.hubspot.com/oauth/authorize",
        "client_auth_style": "body",
        "flow": "authorization_code",
        "optional_scope_parameter": "optional_scope",
        "pkce_mode": "unavailable",
        "provider_key": "hubspot",
        "refresh_token_response_requirements": ("refresh_token", "expires_in"),
        "response_account_id_field": "hub_id",
        "response_metadata_fields": (
            "hub_id",
            "user_id",
            "app_id",
            "hub_domain",
            "is_private_distribution",
        ),
        "response_scope_fields": ("scopes", "scope"),
        "scopes": (
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.companies.read",
            "crm.objects.companies.write",
            "crm.objects.deals.read",
            "crm.objects.deals.write",
            "crm.objects.owners.read",
            "crm.schemas.contacts.read",
            "crm.schemas.companies.read",
            "crm.schemas.deals.read",
        ),
        "token_endpoint": "https://api.hubspot.com/oauth/2026-03/token",
    },
    "linear": {
        "authorization_code_response_requirements": (
            "refresh_token",
            "expires_in",
            "scope_evidence",
        ),
        "authorization_endpoint": "https://linear.app/oauth/authorize",
        "authorization_params": (("actor", "user"),),
        "client_auth_style": "body",
        "flow": "authorization_code",
        "pkce_mode": "required",
        "provider_key": "linear",
        "refresh_token_response_requirements": ("refresh_token", "expires_in", "scope_evidence"),
        "required_scope_subset": ("read", "write"),
        "required_token_type": "Bearer",
        "response_account_id_field": None,
        "response_scope_fields": ("scope",),
        "scope_separator": ",",
        "scopes": ("read", "write"),
        "token_endpoint": "https://api.linear.app/oauth/token",
    },
    "meta-ads": {
        "authorization_endpoint": "https://www.facebook.com/v25.0/dialog/oauth",
        "flow": "authorization_code",
        "hook": "meta-long-lived",
        "pkce_mode": "unavailable",
        "provider_key": "meta-ads",
        "scopes": ("ads_management", "ads_read", "business_management"),
        "token_endpoint": "https://graph.facebook.com/v25.0/oauth/access_token",
    },
    "microsoft-365": {
        "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "authorization_params": (("response_mode", "query"),),
        "flow": "authorization_code",
        "pkce_mode": "required",
        "provider_key": "microsoft-365",
        "scopes": (
            "offline_access",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Calendars.ReadWrite",
        ),
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    },
    "outreach": {
        "authorization_endpoint": "https://api.outreach.io/oauth/authorize",
        "flow": "authorization_code",
        "pkce_mode": "unavailable",
        "provider_key": "outreach",
        "scopes": ("sequenceStates.write",),
        "token_endpoint": "https://api.outreach.io/oauth/token",
    },
    "pipedrive": {
        "authorization_endpoint": "https://oauth.pipedrive.com/oauth/authorize",
        "client_auth_style": "basic",
        "flow": "authorization_code",
        "include_response_type": False,
        "pkce_mode": "unavailable",
        "provider_key": "pipedrive",
        "response_metadata_fields": ("api_domain",),
        "token_endpoint": "https://oauth.pipedrive.com/oauth/token",
    },
    "reddit": {
        "client_auth_style": "basic",
        "flow": "client_credentials",
        "provider_key": "reddit",
        "token_endpoint": "https://www.reddit.com/api/v1/access_token",
    },
    "salesforce": {
        "authorization_endpoint": "https://login.salesforce.com/services/oauth2/authorize",
        "flow": "authorization_code",
        "pkce_mode": "required",
        "provider_key": "salesforce",
        "response_metadata_fields": ("instance_url", "id"),
        "scopes": ("api", "refresh_token"),
        "token_endpoint": "https://login.salesforce.com/services/oauth2/token",
    },
    "salesloft": {
        "authorization_endpoint": "https://accounts.salesloft.com/oauth/authorize",
        "flow": "authorization_code",
        "pkce_mode": "unavailable",
        "provider_key": "salesloft",
        "token_endpoint": "https://accounts.salesloft.com/oauth/token",
    },
    "taboola": {
        "client_auth_style": "body",
        "flow": "client_credentials",
        "provider_key": "taboola",
        "token_endpoint": "https://backstage.taboola.com/backstage/oauth/token",
    },
}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_frozen_released_contract_facts(provider):
    method = (
        "client_credentials" if provider in {"taboola", "reddit"} else "oauth2_authorization_code"
    )
    contract = get_auth_contract(provider, method=method)
    for field, expected in RELEASED_CONTRACTS[provider].items():
        assert getattr(contract, field) == expected, (provider, field)
