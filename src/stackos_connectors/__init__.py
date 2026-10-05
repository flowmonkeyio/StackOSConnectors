"""Execute a named connector action using plain data and resolved authentication."""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .contracts import (
    ActionDefinition,
    AuthMethodDefinition,
    CallOptions,
    Connector,
    ConnectorAuth,
    ConnectorFile,
    ConnectorRequest,
    ConnectorResult,
    NativeSession,
    RateLimiter,
    ValidationIssue,
)
from .errors import ConnectorError, IntegrationDownError, RateLimitedError, ValidationError
from .registry import ConnectorClient, ConnectorRegistry


@lru_cache(maxsize=1)
def get_default_client() -> ConnectorClient:
    """Load reviewed builtin descriptions on first use, without provider imports."""
    from .catalog import default_registry

    return ConnectorClient(registry=default_registry())


async def execute(
    connector: str,
    action: str,
    data: Mapping[str, Any],
    auth: ConnectorAuth | Mapping[str, Any] | None = None,
    options: CallOptions | None = None,
) -> ConnectorResult:
    return await get_default_client().execute(connector, action, data, auth, options)


def validate(
    connector: str,
    action: str,
    data: Mapping[str, Any],
    auth: ConnectorAuth | Mapping[str, Any] | None = None,
    options: CallOptions | None = None,
) -> list[ValidationIssue]:
    return get_default_client().validate(connector, action, data, auth, options)


def validate_data(connector: str, action: str, data: Mapping[str, Any]) -> list[ValidationIssue]:
    return get_default_client().validate_data(connector, action, data)


def estimate_cost(
    connector: str,
    action: str,
    data: Mapping[str, Any],
    auth: ConnectorAuth | Mapping[str, Any] | None = None,
    options: CallOptions | None = None,
) -> int:
    return get_default_client().estimate_cost(connector, action, data, auth, options)


def list_connectors() -> list[str]:
    return get_default_client().list_connectors()


def describe(connector: str, action: str | None = None) -> dict[str, Any]:
    return get_default_client().describe(connector, action)


__all__ = [
    "ActionDefinition",
    "AuthMethodDefinition",
    "CallOptions",
    "Connector",
    "ConnectorAuth",
    "ConnectorClient",
    "ConnectorError",
    "ConnectorFile",
    "ConnectorRegistry",
    "ConnectorRequest",
    "ConnectorResult",
    "IntegrationDownError",
    "NativeSession",
    "RateLimitedError",
    "RateLimiter",
    "ValidationError",
    "ValidationIssue",
    "describe",
    "estimate_cost",
    "execute",
    "get_default_client",
    "list_connectors",
    "validate",
    "validate_data",
]
