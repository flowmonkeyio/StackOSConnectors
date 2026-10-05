"""Explicit immutable connector/action routing with lazy implementation loading."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from importlib import import_module
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import (
    ActionDefinition,
    CallOptions,
    Connector,
    ConnectorAuth,
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
    freeze,
    thaw,
)
from .errors import ConnectorError, IntegrationDownError, ValidationError
from .redaction import auth_secret_values, redact_secret_values, redact_secrets
from .validation import schema_issues

type ConnectorFactory = Callable[[], Connector]


def _clean_issues(
    issues: Iterable[ValidationIssue], clean: Callable[[Any], Any]
) -> list[ValidationIssue]:
    safe: list[ValidationIssue] = []
    for item in issues:
        try:
            payload = item.model_dump() if isinstance(item, ValidationIssue) else item
            safe.append(ValidationIssue.model_validate(clean(payload)))
        except Exception:
            safe.append(
                ValidationIssue(path="$", message="invalid connector validation diagnostic")
            )
    return safe


@contextmanager
def _preflight_boundary(
    auth: ConnectorAuth | Mapping[str, Any] | None, phase: str
) -> Iterator[Callable[[Any], Any]]:
    """Sanitize every local preparation/validation/cost diagnostic before it escapes."""
    try:
        secrets = auth_secret_values(auth)
    except (AttributeError, TypeError, ValueError):
        # Invalid auth is diagnosed by _prepare without serializing the raw value.
        secrets = ()

    def clean(value: Any) -> Any:
        return redact_secret_values(redact_secrets(value), secrets)

    try:
        yield clean
    except ValidationError as exc:
        raise ValidationError(
            clean(exc.detail),
            issues=_clean_issues(exc.issues, clean),
            data=clean(exc.data),
        ) from None
    except ConnectorError as exc:
        metadata = clean(exc.metadata_json)
        metadata.pop("outcome", None)
        metadata["provider_executed"] = False
        raise ConnectorError(
            clean(exc.detail),
            provider_status_code=exc.provider_status_code,
            provider_error=clean(exc.provider_error),
            output_json=clean(exc.output_json),
            metadata_json=metadata,
        ) from None
    except IntegrationDownError as exc:
        metadata = clean(exc.data)
        metadata.pop("outcome", None)
        metadata["provider_executed"] = False
        raise ConnectorError(
            clean(exc.detail),
            provider_status_code=exc.data.get("status"),
            provider_error=clean(exc.data.get("provider_error")),
            metadata_json=metadata,
        ) from None
    except Exception:
        raise ConnectorError(
            f"connector {phase} failed", metadata_json={"provider_executed": False}
        ) from None


class ConnectorRegistry:
    def __init__(
        self,
        *,
        actions: Iterable[ActionDefinition] = (),
        implementations: Mapping[str, ConnectorFactory | str] | None = None,
        connector_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        definitions: dict[tuple[str, str], ActionDefinition] = {}
        for action in actions:
            key = (action.connector, action.key)
            if key in definitions:
                raise ValueError(f"duplicate action registration: {action.connector}/{action.key}")
            for schema in (action.input_schema, action.output_schema):
                Draft202012Validator.check_schema(thaw(schema))
            for method in action.auth_methods:
                Draft202012Validator.check_schema(thaw(method.fields_schema))
                Draft202012Validator.check_schema(thaw(method.config_schema))
            definitions[key] = action
        self.actions = MappingProxyType(definitions)
        self.implementations = MappingProxyType(dict(implementations or {}))
        self.connector_metadata = freeze(connector_metadata or {})

    def with_actions(self, actions: Iterable[ActionDefinition]) -> ConnectorRegistry:
        return ConnectorRegistry(
            actions=[*self.actions.values(), *actions],
            implementations=self.implementations,
            connector_metadata=self.connector_metadata,
        )

    def action(self, connector: str, action: str) -> ActionDefinition:
        definition = self.actions.get((connector, action))
        if definition is None:
            raise ValidationError("connector/action is not registered")
        return definition

    def implementation(self, connector: str) -> Connector:
        binding = self.implementations.get(connector)
        if binding is None:
            raise ConnectorError(
                "connector implementation is unavailable",
                metadata_json={"provider_executed": False},
            )
        try:
            if isinstance(binding, str):
                module, separator, name = binding.partition(":")
                if not separator or not module or not name:
                    raise ValueError("implementation binding must be module:factory")
                binding = getattr(import_module(module), name)
            implementation = binding()
        except Exception:
            raise ConnectorError(
                "connector implementation could not be loaded",
                metadata_json={"provider_executed": False},
            ) from None
        if implementation.key != connector:
            raise ConnectorError(
                "connector implementation key does not match registration",
                metadata_json={"provider_executed": False},
            )
        return implementation


class ConnectorClient:
    def __init__(self, *, registry: ConnectorRegistry) -> None:
        self.registry = registry

    def register_actions(self, actions: Iterable[ActionDefinition]) -> ConnectorClient:
        """Return an isolated client with additional named actions, never replacing a binding."""
        return ConnectorClient(registry=self.registry.with_actions(actions))

    def list_connectors(self) -> list[str]:
        return sorted(
            {connector for connector, _ in self.registry.actions}
            | self.registry.connector_metadata.keys()
        )

    def describe(self, connector: str, action: str | None = None) -> dict[str, Any]:
        definitions = (
            [self.registry.action(connector, action)]
            if action is not None
            else [item for item in self.registry.actions.values() if item.connector == connector]
        )
        if not definitions and connector not in self.registry.connector_metadata:
            raise ValidationError("connector is not registered")
        metadata = self.registry.connector_metadata.get(connector, {})
        method_metadata = {
            method["key"]: method
            for method in metadata.get("auth_methods", ())
            if isinstance(method, Mapping) and isinstance(method.get("key"), str)
        }
        return {
            **thaw(metadata),
            "connector": connector,
            "available": connector in self.registry.implementations,
            "actions": [
                {
                    **({"icon": thaw(metadata["icon"])} if "icon" in metadata else {}),
                    **thaw(item.metadata),
                    "key": item.key,
                    "operation": item.operation,
                    "config": thaw(item.config),
                    "description": item.description,
                    "guidance": item.guidance,
                    "input_schema": thaw(item.input_schema),
                    "output_schema": thaw(item.output_schema),
                    "examples": thaw(item.examples),
                    "auth_optional": item.auth_optional,
                    "auth_methods": [
                        {
                            **thaw(method_metadata.get(method.key, {})),
                            "key": method.key,
                            "description": method.description,
                            "fields_schema": thaw(method.fields_schema),
                            "config_schema": thaw(method.config_schema),
                        }
                        for method in item.auth_methods
                    ],
                }
                for item in definitions
            ],
        }

    def validate_data(
        self, connector: str, action: str, data: Mapping[str, Any]
    ) -> list[ValidationIssue]:
        """Validate declared routing/input before the caller resolves authentication."""
        definition = self.registry.action(connector, action)
        if not isinstance(data, Mapping):
            return [ValidationIssue(path="$", message="data must be an object")]
        try:
            detached = thaw(freeze(data))
        except TypeError:
            return [ValidationIssue(path="$", message="data must be JSON-compatible")]
        return schema_issues(definition.input_schema, detached)

    def _prepare(
        self,
        connector: str,
        action: str,
        data: Mapping[str, Any],
        auth: ConnectorAuth | Mapping[str, Any] | None,
        options: CallOptions | None,
    ) -> tuple[ConnectorRequest, Connector]:
        definition = self.registry.action(connector, action)
        if not isinstance(data, Mapping):
            raise ValidationError("data must be an object")
        issues = self.validate_data(connector, action, data)
        if auth is not None and not isinstance(auth, ConnectorAuth):
            try:
                auth = ConnectorAuth(**dict(auth))
            except (ValueError, TypeError):
                raise ValidationError(
                    "auth must contain method, fields and optional config"
                ) from None
        if definition.auth_methods:
            method = next(
                (
                    item
                    for item in definition.auth_methods
                    if auth is not None and item.key == auth.method
                ),
                None,
            )
            if auth is None and definition.auth_optional:
                pass
            elif method is None:
                issues.append(
                    ValidationIssue(path="$.auth.method", message="select a supported auth method")
                )
            else:
                issues += schema_issues(
                    method.fields_schema, thaw(auth.fields), path="$.auth.fields"
                )
                issues += schema_issues(
                    method.config_schema, thaw(auth.config), path="$.auth.config"
                )
        elif auth is not None:
            issues.append(
                ValidationIssue(path="$.auth", message="action does not accept authentication")
            )
        if issues:
            raise ValidationError("connector input validation failed", issues=issues)
        if options is not None and not isinstance(options, CallOptions):
            raise ValidationError("options must be CallOptions")
        request = ConnectorRequest(
            connector,
            action,
            definition.operation,
            thaw(data),
            definition.config,
            auth,
            options or CallOptions(),
        )
        implementation = self.registry.implementation(connector)
        return request, implementation

    def validate(
        self,
        connector: str,
        action: str,
        data: Mapping[str, Any],
        auth: ConnectorAuth | Mapping[str, Any] | None = None,
        options: CallOptions | None = None,
    ) -> list[ValidationIssue]:
        try:
            with _preflight_boundary(auth, "validation") as clean:
                request, implementation = self._prepare(connector, action, data, auth, options)
                return _clean_issues(implementation.validate(request), clean)
        except ValidationError as exc:
            return exc.issues or [ValidationIssue(path="$", message=exc.detail)]

    def estimate_cost(
        self,
        connector: str,
        action: str,
        data: Mapping[str, Any],
        auth: ConnectorAuth | Mapping[str, Any] | None = None,
        options: CallOptions | None = None,
    ) -> int:
        with _preflight_boundary(auth, "cost estimation"):
            request, implementation = self._prepare(connector, action, data, auth, options)
            issues = implementation.validate(request)
            if issues:
                raise ValidationError("connector input validation failed", issues=issues)
            return implementation.estimate_cost_cents(request)

    async def execute(
        self,
        connector: str,
        action: str,
        data: Mapping[str, Any],
        auth: ConnectorAuth | Mapping[str, Any] | None = None,
        options: CallOptions | None = None,
    ) -> ConnectorResult:
        with _preflight_boundary(auth, "validation") as clean:
            request, implementation = self._prepare(connector, action, data, auth, options)
            issues = implementation.validate(request)
            if issues:
                raise ValidationError("connector input validation failed", issues=issues)

        if request.options.progress_callback is not None:
            callback = request.options.progress_callback
            request = replace(
                request,
                options=replace(
                    request.options, progress_callback=lambda payload: callback(clean(payload))
                ),
            )
        try:
            result = await implementation.execute(request)
        except ConnectorError as exc:
            if isinstance(exc, ValidationError):
                raise ValidationError(
                    clean(exc.detail),
                    data=clean(exc.data),
                    metadata_json=clean(exc.metadata_json),
                    issues=[
                        ValidationIssue.model_validate(clean(item.model_dump()))
                        for item in exc.issues
                    ],
                ) from None
            raise ConnectorError(
                clean(exc.detail),
                provider_status_code=exc.provider_status_code,
                provider_error=clean(exc.provider_error),
                output_json=clean(exc.output_json),
                metadata_json=clean(exc.metadata_json),
            ) from None
        except IntegrationDownError as exc:
            raise ConnectorError(
                clean(exc.detail),
                provider_status_code=exc.data.get("status"),
                provider_error=clean(exc.data.get("provider_error")),
                metadata_json=clean(exc.data),
            ) from None
        except Exception:
            # No added retries: a protocol/callback exception may follow an external side effect.
            raise ConnectorError(
                "connector execution failed",
                metadata_json={
                    "outcome": "unknown",
                    "retry_safe": False,
                },
            ) from None
        payload = clean(result.model_dump())
        # Native response facts may include signed download URLs and opaque next-page
        # tokens. Display/audit projection belongs to the caller; credential echoes
        # remain forbidden even in the in-process response.
        payload["output_json"] = redact_secret_values(
            result.output_json, auth_secret_values(request.auth)
        )
        return ConnectorResult.model_validate(payload)
