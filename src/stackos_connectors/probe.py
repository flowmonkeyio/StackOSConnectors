"""Explicit provider probes and safe evidence; no lifecycle, custody or policy."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping
from contextlib import nullcontext
from types import ModuleType
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .contracts import CallOptions, ConnectorAuth, ConnectorRequest, thaw
from .errors import IntegrationDownError, RateLimitedError, ValidationError
from .redaction import auth_secret_values, redact_secret_values, redact_secrets
from .validation import schema_issues


class PermissionVerification(BaseModel):
    evidence_source: Literal["oauth_response", "provider_probe", "unavailable"]
    model_config = ConfigDict(extra="forbid")


class AuthMethodProbeContext(BaseModel):
    auth_method_key: str
    permission_verification: PermissionVerification | None = None


class AuthProbeAccountEvidence(BaseModel):
    provider_account_id: str | None = None
    display_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthProbeEvidence(BaseModel):
    grants: list[str] | None = None
    account: AuthProbeAccountEvidence | None = None


def _metadata(connector: str) -> Mapping[str, Any]:
    from . import get_default_client

    metadata = get_default_client().registry.connector_metadata.get(connector)
    if metadata is None:
        raise ValidationError("unknown connector for credential probe")
    return metadata


def _binding(metadata: Mapping[str, Any]) -> tuple[ModuleType, Any]:
    binding = metadata.get("probe_implementation")
    if not isinstance(binding, str) or not re.fullmatch(
        r"stackos_connectors\.connectors\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*",
        binding,
    ):
        raise ValidationError("connector has no credential probe")
    module_name, name = binding.split(":", 1)
    module = importlib.import_module(module_name)
    return module, getattr(module, name)


def project_probe_config(connector: str, config: Mapping[str, Any]) -> dict[str, Any]:
    """Project portable inputs, preserving provider-owned legacy probe aliases.

    Host-only refs/policy are never returned. This function performs no probe.
    """
    metadata = _metadata(connector)
    module, _ = _binding(metadata)
    project = getattr(module, "project_config", None)
    projected = project(config) if project is not None else dict(config)
    keys = {
        key
        for method in metadata.get("auth_methods", ())
        for key in (
            *method.get("config_schema", {}).get("properties", {}),
            *method.get("config_schema", {}).get("required", ()),
        )
    }
    return {key: value for key, value in projected.items() if key in keys}


def probe_request(connector: str, auth: ConnectorAuth, options: CallOptions) -> ConnectorRequest:
    """Use existing pure provider input helpers without executing an action."""
    return ConnectorRequest(
        connector=connector,
        action_key="credential.probe",
        operation="credential.probe",
        input_json={},
        config_json={},
        auth=auth,
        options=options,
    )


async def probe_credentials(
    connector: str,
    *,
    auth: ConnectorAuth,
    options: CallOptions | None = None,
    context: AuthMethodProbeContext | None = None,
) -> dict[str, Any]:
    """Probe an already resolved credential with the caller's saved auth method.

    No token acquisition/refresh, readiness decision, credential lookup or storage
    occurs here. The caller owns injected HTTP and rate-limiter lifetimes.
    """
    metadata = _metadata(connector)
    declaration = next(
        (item for item in metadata.get("auth_methods", ()) if item.get("key") == auth.method),
        None,
    )
    if declaration is None:
        raise ValidationError("saved auth method has no credential probe contract")
    if context is not None and context.auth_method_key != auth.method:
        raise ValidationError("probe context must match the resolved auth method")
    auth = ConnectorAuth(
        method=auth.method, fields=auth.fields, config=project_probe_config(connector, auth.config)
    )
    issues = schema_issues(
        declaration.get("fields_schema", {}), thaw(auth.fields), path="$.auth.fields"
    )
    issues += schema_issues(
        declaration.get("config_schema", {}), thaw(auth.config), path="$.auth.config"
    )
    if issues:
        raise ValidationError("credential probe inputs are invalid", issues=issues)
    module, factory = _binding(metadata)
    options = options or CallOptions()
    context = context or AuthMethodProbeContext(auth_method_key=auth.method)
    sensitive = auth_secret_values(auth)
    try:
        if not isinstance(factory, type):
            raw = await factory(auth=auth, options=options, context=context)
        else:
            payload_format = declaration.get("payload_format", "json")
            if payload_format == "raw":
                payload = str(auth.fields[declaration["payload_field"]]).encode()
            elif payload_format == "none":
                payload = b""
            else:
                payload = json.dumps(thaw(auth.fields)).encode()
            build_kwargs = getattr(module, "constructor_kwargs", None)
            extra = build_kwargs(auth, options) if build_kwargs is not None else {}
            extra.setdefault("timeout", options.timeout)
            async with (
                nullcontext(options.http)
                if options.http is not None
                else httpx.AsyncClient(timeout=options.timeout or 30.0)
            ) as http:
                integration = factory(
                    payload=payload,
                    http=http,
                    probe_context=context,
                    rate_limiter=options.rate_limiter,
                    **extra,
                )
                raw = await integration.test_credentials()
    except IntegrationDownError as exc:
        from .shared.google.probe import diagnostic_facts

        data = {
            key: value
            for key, value in exc.data.items()
            if key in {"status", "stage", "reason_code", "reply_code", "retry_after", "retryable"}
        }
        data.update(diagnostic_facts(connector, exc.data))
        error_type = RateLimitedError if isinstance(exc, RateLimitedError) else IntegrationDownError
        raise error_type(
            "Provider credential probe failed.",
            data=redact_secret_values(redact_secrets(data), sensitive),
        ) from None
    except ValidationError:
        raise
    except Exception:
        raise IntegrationDownError(
            "Provider credential probe failed.",
            data={"stage": "test", "reason_code": "probe_error"},
        ) from None
    if not isinstance(raw, Mapping):
        raise IntegrationDownError("Provider credential probe returned an invalid result.")
    return redact_secret_values(redact_secrets(dict(raw)), sensitive)
