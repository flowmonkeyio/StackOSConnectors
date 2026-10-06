"""Shared stateless OAuth request/response protocol; no token lifecycle or storage."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote_plus, urlparse

import httpx

from ..auth import OAuthProviderContract, OAuthTokenError, TokenResult
from ..contracts import ConnectorAuth

_MAX_TOKEN_RESPONSE_BYTES = 1_000_000
_RETRYABLE_OAUTH_ERRORS = {"server_error", "slow_down", "temporarily_unavailable"}


def _error(
    contract, detail, *, category="invalid_response", retryable=True, status=None, fields=()
):
    return OAuthTokenError(
        detail,
        provider_key=contract.provider_key,
        category=category,
        retryable=retryable,
        repair_required=not retryable,
        status_code=status,
        invalid_fields=fields,
    )


def _response_scopes(contract, body):
    present = any(name in body for name in contract.response_scope_fields)
    raw = next((body[name] for name in contract.response_scope_fields if name in body), None)
    if isinstance(raw, str):
        return tuple(unquote_plus(raw.replace(",", " ")).split()), present
    if isinstance(raw, list):
        return tuple(str(scope).strip() for scope in raw if str(scope).strip()), present
    return None, present


def _response_config(contract, body):
    result = {}
    for response_field, config_field, suffix in contract.response_config_fields:
        if not (value := body.get(response_field)):
            continue
        if not isinstance(value, str) or not value.strip():
            raise _error(contract, "provider authorization response metadata is invalid")
        try:
            parsed = urlparse(value.strip())
            hostname = (parsed.hostname or "").lower()
            valid = (
                parsed.scheme == "https"
                and hostname.endswith(suffix)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
            )
        except (ValueError, RecursionError):
            valid = False
        if not valid:
            raise _error(contract, "provider authorization response metadata is invalid") from None
        result[config_field] = f"https://{parsed.netloc}"
    return result


def decode_response(response: httpx.Response, contract: OAuthProviderContract) -> dict[str, Any]:
    if len(response.content) > _MAX_TOKEN_RESPONSE_BYTES:
        raise _error(
            contract, "provider authorization response is too large", status=response.status_code
        )
    if response.status_code >= 300:
        try:
            body = response.json()
        except ValueError:
            body = None
        code = body.get("error") if isinstance(body, dict) else None
        retryable = (
            response.status_code in {408, 425, 429}
            or response.status_code >= 500
            or (isinstance(code, str) and code.strip().lower() in _RETRYABLE_OAUTH_ERRORS)
        )
        raise _error(
            contract,
            "provider authorization exchange failed",
            category="provider_rejected",
            retryable=retryable,
            status=response.status_code,
        )
    try:
        body = response.json()
    except (ValueError, RecursionError):
        raise _error(
            contract, "provider authorization response is not JSON", status=response.status_code
        ) from None
    if not isinstance(body, dict):
        raise _error(
            contract,
            "provider authorization response must be an object",
            status=response.status_code,
        )
    if not isinstance(body.get("access_token"), str) or not body["access_token"].strip():
        raise _error(
            contract,
            "provider authorization response is missing access material",
            status=response.status_code,
        )
    try:
        _response_config(contract, body)
    except OAuthTokenError:
        raise _error(
            contract,
            "provider authorization response metadata is invalid",
            status=response.status_code,
        ) from None
    return body


def token_result(contract: OAuthProviderContract, body: dict, grant_type: str) -> TokenResult:
    requirements = ()
    invalid = []
    if grant_type == "authorization_code":
        field = contract.response_account_id_field
        if field in contract.response_metadata_fields:
            value = body.get(field)
            if not (
                (isinstance(value, str) and value.strip())
                or (isinstance(value, int) and not isinstance(value, bool))
            ):
                invalid.append(field)
        requirements = contract.authorization_code_response_requirements
    elif grant_type == "refresh_token":
        requirements = contract.refresh_token_response_requirements
    elif contract.flow == "jwt_bearer":
        requirements = ("expires_in",)
    scopes, scopes_present = _response_scopes(contract, body)
    for requirement in requirements:
        value = body.get(requirement)
        if (
            (requirement == "refresh_token" and (not isinstance(value, str) or not value.strip()))
            or (
                requirement == "expires_in"
                and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value <= 0
                    or value
                    > (datetime.max - datetime.now(UTC).replace(tzinfo=None)).total_seconds()
                )
            )
            or (requirement == "scope_evidence" and not scopes)
        ):
            invalid.append(requirement)
    if contract.required_token_type is not None:
        value = body.get("token_type")
        if (
            not isinstance(value, str)
            or value.strip().casefold() != contract.required_token_type.casefold()
        ):
            invalid.append("token_type")
    if contract.flow == "jwt_bearer" and "scope" in body:
        value = body["scope"]
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(ord(c) < 32 or ord(c) > 126 for c in value)
        ):
            invalid.append("scope")
    if contract.required_scope_subset and not set(contract.required_scope_subset).issubset(
        scopes or ()
    ):
        invalid.append("scope")
    if invalid:
        raise _error(
            contract,
            "provider token response violates its OAuth protocol contract",
            category="invalid_response",
            retryable=False,
            fields=invalid,
        )
    metadata = {
        name: body[name]
        for name in contract.response_metadata_fields
        if name in body and isinstance(body[name], str | int | float | bool)
    }
    account = metadata.get(contract.response_account_id_field)
    refresh = body.get("refresh_token")
    expiry = body.get("expires_in")
    token_type = body.get("token_type")
    # Preserve optional-field handling of the released consumer, without inventing grants.
    return TokenResult(
        access_token=body["access_token"].strip(),
        refresh_token=refresh.strip() if isinstance(refresh, str) and refresh.strip() else None,
        expires_in=expiry
        if isinstance(expiry, int | float)
        and not isinstance(expiry, bool)
        and math.isfinite(expiry)
        and expiry > 0
        else None,
        token_type=token_type if isinstance(token_type, str) else None,
        scopes=scopes,
        scopes_present=scopes_present,
        account_id=str(account) if account is not None else None,
        metadata=metadata,
        config_updates=_response_config(contract, body),
    )


async def request_token(
    *,
    contract,
    auth: ConnectorAuth,
    grant_type,
    code,
    redirect_uri,
    code_verifier,
    refresh_token,
    http,
    timeout,
):
    if grant_type not in contract.grant_types:
        raise OAuthTokenError(
            "auth method does not support this grant", provider_key=contract.provider_key
        )
    if (
        not isinstance(timeout, int | float)
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise OAuthTokenError(
            "timeout must be a positive finite number", provider_key=contract.provider_key
        )
    data = {"grant_type": grant_type, **dict(contract.token_params)}
    if grant_type == "authorization_code":
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(redirect_uri, str)
            or not redirect_uri
            or refresh_token is not None
        ):
            raise OAuthTokenError(
                "authorization code and callback URI are required",
                provider_key=contract.provider_key,
            )
        if contract.pkce_mode == "required" and not code_verifier:
            raise OAuthTokenError(
                "provider requires a PKCE verifier", provider_key=contract.provider_key
            )
        if code_verifier is not None and (not isinstance(code_verifier, str) or not code_verifier):
            raise OAuthTokenError("PKCE verifier is invalid", provider_key=contract.provider_key)
        if contract.pkce_mode == "unavailable" and code_verifier is not None:
            raise OAuthTokenError(
                "provider does not support PKCE", provider_key=contract.provider_key
            )
        data.update(code=code, redirect_uri=redirect_uri)
        if code_verifier:
            data["code_verifier"] = code_verifier
    else:
        if any(value is not None for value in (code, redirect_uri, code_verifier)):
            raise OAuthTokenError(
                "irrelevant authorization-code grant fields", provider_key=contract.provider_key
            )
        if grant_type == "refresh_token":
            if not isinstance(refresh_token, str) or not refresh_token.strip():
                raise OAuthTokenError(
                    "renewal material is missing", provider_key=contract.provider_key
                )
            data["refresh_token"] = refresh_token.strip()
        elif refresh_token is not None:
            raise OAuthTokenError(
                "irrelevant refresh grant field", provider_key=contract.provider_key
            )
    if grant_type == "jwt_bearer":
        from .google.service_account import JWT_GRANT, sign_assertion

        data = {
            "grant_type": JWT_GRANT,
            "assertion": sign_assertion(
                value=auth.fields.get("service_account_json"),
                scopes=contract.scopes,
                subject=contract.delegated_subject,
            ),
        }
    elif grant_type == "client_credentials" and contract.scopes:
        data["scope"] = contract.scope_separator.join(contract.scopes)
    client_id, client_secret = auth.fields.get("client_id"), auth.fields.get("client_secret")
    if grant_type != "jwt_bearer":
        if (
            not isinstance(client_id, str)
            or not client_id
            or not isinstance(client_secret, str)
            or not client_secret
        ):
            raise OAuthTokenError(
                "OAuth application fields are missing", provider_key=contract.provider_key
            )
        if contract.client_auth_style != "basic":
            data.update(client_id=client_id, client_secret=client_secret)
    user_agent = auth.fields.get("user_agent")
    headers = (
        {"User-Agent": user_agent.strip()}
        if isinstance(user_agent, str) and user_agent.strip()
        else None
    )
    authentication = (
        httpx.BasicAuth(client_id, client_secret) if contract.client_auth_style == "basic" else None
    )
    owned = http is None
    client = http or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        response = await client.post(
            contract.token_endpoint,
            data=data,
            auth=authentication,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )
        body = decode_response(response, contract)
        if grant_type == "authorization_code" and contract.hook == "meta-long-lived":
            from ..connectors.meta_ads.auth import exchange_long_lived

            body = await exchange_long_lived(client, contract, auth, body["access_token"], timeout)
        return token_result(contract, body, grant_type)
    except httpx.HTTPError:
        raise _error(
            contract, "provider authorization transport failed", category="transport"
        ) from None
    finally:
        if owned:
            await client.aclose()
