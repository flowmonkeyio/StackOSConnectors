"""Execute a named connector action using plain data and resolved authentication."""

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

# Builtin composition is installed by the provider catalog, never by importing host code.
_default_client = ConnectorClient(registry=ConnectorRegistry())
execute = _default_client.execute
validate = _default_client.validate
validate_data = _default_client.validate_data
estimate_cost = _default_client.estimate_cost
list_connectors = _default_client.list_connectors
describe = _default_client.describe

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
    "list_connectors",
    "validate",
    "validate_data",
]
