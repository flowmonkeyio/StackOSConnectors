"""Explicit provider authentication protocols; the caller owns every lifecycle decision.

These functions never store credentials, launch consent, choose when to renew, or
enforce application readiness. Token results are sensitive in-process values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from importlib import import_module
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from .contracts import ConnectorAuth, freeze, thaw


class OAuthTokenError(ValueError):
    """Allowlisted protocol diagnostics, never provider bodies or request objects."""

    def __init__(
        self,
        detail: str,
        *,
        provider_key: str | None = None,
        category: str = "invalid_input",
        retryable: bool = False,
        repair_required: bool = False,
        status_code: int | None = None,
        invalid_fields: Sequence[str] = (),
    ) -> None:
        super().__init__(detail)
        self.provider_key = provider_key
        self.category = category
        self.retryable = retryable
        self.repair_required = repair_required
        self.status_code = status_code
        self.invalid_fields = tuple(invalid_fields)


@dataclass(frozen=True)
class OAuthProviderContract:
    provider_key: str
    flow: Literal["authorization_code", "client_credentials", "jwt_bearer"]
    token_endpoint: str
    authorization_endpoint: str | None = None
    include_response_type: bool = True
    scopes: tuple[str, ...] = ()
    scope_separator: str = " "
    optional_scope_parameter: str | None = None
    client_auth_style: Literal["body", "basic"] = "body"
    pkce_mode: Literal["required", "supported", "unavailable"] = "unavailable"
    authorization_params: tuple[tuple[str, str], ...] = ()
    token_params: tuple[tuple[str, str], ...] = ()
    response_metadata_fields: tuple[str, ...] = ()
    response_scope_fields: tuple[str, ...] = ("scope",)
    response_account_id_field: str | None = "id"
    authorization_code_response_requirements: tuple[str, ...] = ()
    refresh_token_response_requirements: tuple[str, ...] = ()
    required_token_type: str | None = None
    required_scope_subset: tuple[str, ...] = ()
    hook: str | None = None
    delegated_subject: str | None = field(default=None, repr=False)
    grant_types: tuple[str, ...] = ()
    response_config_fields: tuple[tuple[str, str, str], ...] = ()
    optional_scope_bundles: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            if isinstance(value, Mapping | list | tuple):
                object.__setattr__(self, descriptor.name, freeze(value))


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str = field(repr=False)
    required_scopes: tuple[str, ...] = ()
    optional_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenResult:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_in: int | float | None = None
    token_type: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] | None = field(default=None, repr=False)
    scopes_present: bool = False
    account_id: str | None = field(default=None, repr=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    config_updates: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze(self.metadata))
        object.__setattr__(self, "config_updates", freeze(self.config_updates))
        if self.scopes is not None:
            object.__setattr__(self, "scopes", tuple(self.scopes))


def _declaration(connector: str, method: str) -> tuple[Mapping, Mapping]:
    from . import get_default_client

    metadata = get_default_client().registry.connector_metadata.get(connector)
    if metadata is None:
        raise OAuthTokenError("connector has no supported authentication protocol")
    declaration = next(
        (item for item in metadata.get("auth_methods", ()) if item["key"] == method),
        None,
    )
    if declaration is None or "protocol" not in declaration or "auth_protocol" not in metadata:
        raise OAuthTokenError(
            "auth method has no supported authentication protocol", provider_key=connector
        )
    return metadata, declaration


def get_auth_contract(
    connector: str,
    *,
    method: str,
    config: Mapping[str, Any] | None = None,
) -> OAuthProviderContract:
    """Read trusted provider facts; configuration cannot replace protocol endpoints."""
    metadata, declaration = _declaration(connector, method)
    data = thaw(metadata["auth_protocol"])
    method_data = thaw(declaration["protocol"])
    direct_scopes = method_data.pop("direct_scopes", None)
    data.update(method_data)
    contract = OAuthProviderContract(provider_key=connector, **data)
    if contract.flow == "jwt_bearer":
        from .shared.google.service_account import delegated_subject

        subject = delegated_subject(connector, (config or {}).get("delegated_subject"))
        contract = replace(contract, delegated_subject=subject)
        if subject is None and direct_scopes is not None:
            contract = replace(contract, scopes=tuple(direct_scopes))
    if binding := metadata.get("auth_implementation"):
        module_name, function_name = binding.split(":", 1)
        contract = getattr(import_module(module_name), function_name)(contract, config or {})
    return contract


def build_authorization_request(
    connector: str,
    *,
    auth: ConnectorAuth,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
    optional_scopes: Sequence[str] = (),
) -> AuthorizationRequest:
    """Format one consent URL using caller-generated state and PKCE challenge."""
    contract = get_auth_contract(connector, method=auth.method, config=auth.config)
    if "authorization_code" not in contract.grant_types or not contract.authorization_endpoint:
        raise OAuthTokenError(
            "auth method does not support authorization-code consent", provider_key=connector
        )
    client_id = auth.fields.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise OAuthTokenError("OAuth application id is missing", provider_key=connector)
    if (
        not isinstance(redirect_uri, str)
        or not redirect_uri
        or not isinstance(state, str)
        or not state
    ):
        raise OAuthTokenError("callback URI and state are required", provider_key=connector)
    if code_challenge is not None and (not isinstance(code_challenge, str) or not code_challenge):
        raise OAuthTokenError("PKCE challenge is invalid", provider_key=connector)
    if contract.pkce_mode == "required" and not code_challenge:
        raise OAuthTokenError("provider requires PKCE S256", provider_key=connector)
    if contract.pkce_mode == "unavailable" and code_challenge is not None:
        raise OAuthTokenError("provider does not support PKCE", provider_key=connector)
    allowed_optional = {
        scope
        for bundle in contract.optional_scope_bundles.values()
        for scope in bundle.get("optional_scopes", ())
    }
    if isinstance(optional_scopes, str) or any(
        not isinstance(s, str) or s not in allowed_optional for s in optional_scopes
    ):
        raise OAuthTokenError("optional scopes must be provider-declared", provider_key=connector)
    optional = tuple(dict.fromkeys(optional_scopes))
    if optional and not contract.optional_scope_parameter:
        raise OAuthTokenError(
            "provider does not support optional consent scopes", provider_key=connector
        )
    query = {"client_id": client_id.strip(), "redirect_uri": redirect_uri, "state": state}
    if contract.include_response_type:
        query["response_type"] = "code"
    if contract.scopes:
        query["scope"] = contract.scope_separator.join(contract.scopes)
    if optional and contract.optional_scope_parameter:
        query[contract.optional_scope_parameter] = contract.scope_separator.join(optional)
    query.update(contract.authorization_params)
    if code_challenge:
        query.update(code_challenge=code_challenge, code_challenge_method="S256")
    return AuthorizationRequest(
        f"{contract.authorization_endpoint}?{urlencode(query)}",
        contract.scopes,
        optional,
    )


async def request_token(
    connector: str,
    *,
    auth: ConnectorAuth,
    grant_type: Literal["authorization_code", "refresh_token", "client_credentials", "jwt_bearer"],
    code: str | None = None,
    redirect_uri: str | None = None,
    code_verifier: str | None = None,
    refresh_token: str | None = None,
    http: httpx.AsyncClient | None = None,
    timeout: float = 30,
) -> TokenResult:
    """Perform exactly one explicit grant (including a declared provider second exchange)."""
    from .shared.oauth import request_token as perform

    contract = get_auth_contract(connector, method=auth.method, config=auth.config)
    return await perform(
        contract=contract,
        auth=auth,
        grant_type=grant_type,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        refresh_token=refresh_token,
        http=http,
        timeout=timeout,
    )


__all__ = [
    "AuthorizationRequest",
    "OAuthProviderContract",
    "OAuthTokenError",
    "TokenResult",
    "build_authorization_request",
    "get_auth_contract",
    "request_token",
]
