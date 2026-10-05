"""Load portable connector descriptions and lazy bindings from packaged JSON.

Provider packages use ``load_registry("provider.json")`` during development;
the default registry reads the reviewed resource list in ``catalog/index.json``.
Execution auth schemas describe resolved credentials, while setup metadata is
retained separately for discovery and never used as an execution schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .contracts import ActionDefinition, AuthMethodDefinition, freeze, thaw
from .registry import ConnectorRegistry

_RESOURCE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\.json\Z")
_ACTION_FIELDS = {
    "key",
    "operation",
    "input_schema",
    "output_schema",
    "auth_methods",
    "config",
    "description",
    "guidance",
    "examples",
    "metadata",
    "auth_optional",
}


def _read(resource: Traversable) -> Any:
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read connector catalog resource: {resource.name}") from exc


def _validate(document: Any, definition: str) -> None:
    schema = _read(files("stackos_connectors").joinpath("catalog", "schema.json"))
    validator = Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})
    error = next(validator.iter_errors(document), None)
    if error is not None:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise ValueError(f"invalid connector catalog {definition} at {path}")


def registry_from_documents(documents: Iterable[Mapping[str, Any]]) -> ConnectorRegistry:
    """Build one immutable registry without importing any provider implementation."""
    actions: list[ActionDefinition] = []
    implementations: dict[str, str] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for original in documents:
        document = thaw(freeze(original))
        _validate(document, "connector")
        key = document["connector"]
        if key in metadata:
            raise ValueError(f"duplicate connector registration: {key}")
        methods: dict[str, AuthMethodDefinition] = {}
        for method in document["auth_methods"]:
            if method["key"] in methods:
                raise ValueError(f"duplicate auth method registration: {key}/{method['key']}")
            methods[method["key"]] = AuthMethodDefinition(
                key=method["key"],
                fields_schema=method["fields_schema"],
                config_schema=method.get("config_schema", {"type": "object"}),
                description=method.get("description", ""),
            )
            try:
                Draft202012Validator.check_schema(method["fields_schema"])
                Draft202012Validator.check_schema(method.get("config_schema", {"type": "object"}))
            except SchemaError as exc:
                raise ValueError("invalid connector auth schema") from exc
        metadata[key] = {
            **document.get("metadata", {}),
            **{
                name: value
                for name, value in document.items()
                if name not in {"connector", "implementation", "actions", "metadata"}
            },
        }
        if binding := document.get("implementation"):
            implementations[key] = binding
        for action in document["actions"]:
            unknown = set(action["auth_methods"]) - methods.keys()
            if unknown:
                raise ValueError(f"action selects an unknown auth method: {key}/{action['key']}")
            extra = {
                **action.get("metadata", {}),
                **{name: value for name, value in action.items() if name not in _ACTION_FIELDS},
            }
            actions.append(
                ActionDefinition(
                    connector=key,
                    key=action["key"],
                    operation=action["operation"],
                    input_schema=action["input_schema"],
                    output_schema=action.get("output_schema", {}),
                    auth_methods=tuple(methods[name] for name in action["auth_methods"]),
                    config=action["config"],
                    description=action.get("description", ""),
                    guidance=action.get("guidance", ""),
                    examples=tuple(action.get("examples", [])),
                    metadata=extra,
                    auth_optional=action.get("auth_optional", False),
                )
            )
    try:
        return ConnectorRegistry(
            actions=actions,
            implementations=implementations,
            connector_metadata=metadata,
        )
    except SchemaError as exc:
        raise ValueError("invalid connector action schema") from exc


def load_registry(*resource_names: str, root: Traversable | None = None) -> ConnectorRegistry:
    """Read explicit provider resources, or the index when none are named.

    ``root`` is an explicit local resource root, useful for a consumer's declared
    catalog and for deterministic tests. Resource names are flat JSON filenames.
    """
    source = root if root is not None else files("stackos_connectors").joinpath("catalog")
    if not resource_names:
        index = _read(source.joinpath("index.json"))
        _validate(index, "index")
        resource_names = tuple(index["connectors"])
    for name in resource_names:
        if not _RESOURCE_NAME.fullmatch(name) or name in {"index.json", "schema.json"}:
            raise ValueError("invalid connector catalog resource name")
    return registry_from_documents(_read(source.joinpath(name)) for name in resource_names)


def default_registry() -> ConnectorRegistry:
    return load_registry()
