"""Plain in-process contracts shared by connector implementations and consumers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


def freeze(value: Any) -> Any:
    """Detach and recursively freeze declarative JSON without changing caller data."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError("declarative values must be JSON-compatible")


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ConnectorAuth:
    """Explicit auth material; execution uses resolved credentials, auth calls use setup fields.

    The caller owns storage and decides when to invoke acquisition or refresh.
    """

    method: str
    fields: Mapping[str, Any] = field(repr=False)
    config: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("auth method must be a nonempty string")
        if not isinstance(self.fields, Mapping) or not isinstance(self.config, Mapping):
            raise ValueError("auth fields and config must be mappings")
        object.__setattr__(self, "fields", freeze(self.fields))
        object.__setattr__(self, "config", freeze(self.config))


@dataclass(frozen=True)
class AuthMethodDefinition:
    key: str
    fields_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    config_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("auth method key is required")
        object.__setattr__(self, "fields_schema", freeze(self.fields_schema))
        object.__setattr__(self, "config_schema", freeze(self.config_schema))


@dataclass(frozen=True)
class ActionDefinition:
    connector: str
    key: str
    operation: str
    input_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    auth_methods: tuple[AuthMethodDefinition, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict, repr=False)
    description: str = ""
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    guidance: str = ""
    examples: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    auth_optional: bool = False

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value for value in (self.connector, self.key, self.operation)
        ):
            raise ValueError("connector, action key and operation are required")
        methods = tuple(self.auth_methods)
        if not isinstance(self.auth_optional, bool):
            raise ValueError("auth_optional must be boolean")
        if len({method.key for method in methods}) != len(methods):
            raise ValueError("duplicate auth method key")
        object.__setattr__(self, "auth_methods", methods)
        for name in ("input_schema", "output_schema", "config", "examples", "metadata"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


class RateLimiter(Protocol):
    async def acquire(self, n: float = 1.0) -> None: ...


class NativeSession(Protocol):
    """Caller-bound native protocol session; account selection remains outside the package."""

    @property
    def files_directory(self) -> Path: ...

    async def request(self, payload: dict[str, Any], *, timeout: float = 30.0) -> Any: ...

    async def wait_message(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class CallOptions:
    provider_context: Mapping[str, Any] = field(default_factory=dict, repr=False)
    idempotency_key: str | None = field(default=None, repr=False)
    correlation_id: str | None = field(default=None, repr=False)
    timeout: float | None = None
    http: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)
    output_dir: Path | None = field(default=None, repr=False)
    progress_callback: Callable[[dict[str, Any]], None] | None = field(
        default=None, repr=False, compare=False
    )
    native_session: NativeSession | None = field(default=None, repr=False, compare=False)
    rate_limiter: RateLimiter | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")
        object.__setattr__(self, "provider_context", freeze(self.provider_context))


@dataclass(frozen=True)
class ConnectorRequest:
    connector: str
    action_key: str
    operation: str
    input_json: dict[str, Any] = field(repr=False)
    config_json: Mapping[str, Any] = field(repr=False)
    auth: ConnectorAuth | None = field(default=None, repr=False)
    options: CallOptions = field(default_factory=CallOptions, repr=False)


class ValidationIssue(BaseModel):
    path: str
    message: str
    code: str = "validation_error"


class ConnectorFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(repr=False)
    mime_type: str | None = None
    name: str | None = None
    size_bytes: int | None = None


class ConnectorResult(BaseModel):
    """Native in-process response facts with resolved credential echoes scrubbed.

    Callers own safe display, persistence and audit projection of output_json,
    including signed URLs and continuation tokens. Diagnostics and files receive
    full key/text redaction. Response data stays hidden from the default repr.
    """

    model_config = ConfigDict(extra="forbid")
    output_json: dict[str, Any] = Field(default_factory=dict, repr=False)
    metadata_json: dict[str, Any] | None = Field(default=None, repr=False)
    cost_cents: int = 0
    files: list[ConnectorFile] = Field(default_factory=list, repr=False)


class Connector(Protocol):
    key: str

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]: ...

    def estimate_cost_cents(self, request: ConnectorRequest) -> int: ...

    async def execute(self, request: ConnectorRequest) -> ConnectorResult: ...
