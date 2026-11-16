"""Fixed native catalog request and immutable server-discovered CFO JSON actions."""

import json
from collections.abc import Mapping
from dataclasses import replace
from functools import lru_cache
from importlib.resources import files

from stackos_connectors.catalog import registry_from_documents
from stackos_connectors.contracts import ActionDefinition, ConnectorResult, thaw
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.operations import declare_operation
from stackos_connectors.shared.provider_utils import issue
from stackos_connectors.validation import schema_issues

from .config import validate_auth
from .contract import eligible, normalize_descriptor, validate_wire
from .transport import execute_request, operation_payload, response_error


@lru_cache
def _auth_method():
    document = json.loads(files(__package__).joinpath("catalog.json").read_text())
    return next(iter(registry_from_documents([document]).actions.values())).auth_methods


def declare_action(key: str, descriptor: Mapping) -> ActionDefinition:
    """Declare a reviewed key-eligible descriptor; no catalog request or state write."""
    descriptor = normalize_descriptor(descriptor)
    schemas, route = descriptor["schemas"], descriptor["route"]
    params = thaw(schemas["params"])
    del params["properties"]["organizationId"]
    params["required"].remove("organizationId")
    params["additionalProperties"] = False
    # Project the complete remaining parameter resource, including local refs.
    declaration = declare_operation(
        connector="cfobooth",
        key=key,
        auth_methods=_auth_method(),
        detail={
            "operation_id": descriptor["operationId"],
            "method": route["method"],
            "path": route["path"],
            "title": descriptor["title"],
            "description": descriptor["description"],
            "path_params_schema": params,
            "path_required": bool(params["required"]),
            "query_params": schemas["query"],
            "query_required": bool(schema_issues(schemas["query"], {})),
            "request_body": schemas["body"],
            "body_required": route["body"] == "json",
            "response": schemas["result"],
            "read_only": descriptor["effect"] == "read",
        },
    )
    return replace(
        declaration,
        config={**declaration.config, "descriptor": descriptor},
        metadata={
            **declaration.metadata,
            **{
                name: descriptor[name]
                for name in (
                    "effect",
                    "replay",
                    "execution",
                    "authentication",
                    "scope",
                    "transport",
                    "responseLimit",
                    "schemaValidation",
                )
            },
        },
    )


def catalog_actions(catalog: Mapping, *, prefix: str = "cfobooth") -> tuple[ActionDefinition, ...]:
    """Validate the whole catalog atomically before returning eligible declarations."""
    catalog = validate_wire(catalog, "catalog-v1")
    operations = catalog["data"]["operations"]
    if len({item["operationId"] for item in operations}) != len(operations):
        raise ValidationError("duplicate CFOBooth operation identity")
    return tuple(
        declare_action(prefix + "." + item["operationId"], item)
        for item in operations
        if eligible(item)
    )


class CFOBoothActionConnector:
    key = "cfobooth"

    def validate(self, request):
        try:
            _, org, _ = validate_auth(request.auth, request.options)
            if request.operation == "operation.execute":
                operation_payload(request, org)
            elif request.operation != "catalog.export":
                raise ValidationError("unsupported CFOBooth operation")
        except ValidationError as exc:
            return [issue("$", exc.detail)]
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        base, org, key = validate_auth(request.auth, request.options)
        if request.operation == "catalog.export":
            method, path, params, body = (
                "GET",
                f"/api/organizations/{org}/operation-catalog",
                [],
                None,
            )
            status, output, metadata = await execute_request(
                request,
                method=method,
                url=base + path,
                key=key,
                params=params,
            )
            try:
                catalog_actions(output)
            except ValidationError:
                raise response_error(status, method, "invalid_catalog") from None
        else:
            method, path, params, body = operation_payload(request, org)
            descriptor = request.config_json["descriptor"]
            status, output, metadata = await execute_request(
                request,
                method=method,
                url=base + path,
                key=key,
                params=params,
                body=body,
                descriptor=descriptor,
            )
            metadata["operation_id"] = descriptor["operationId"]
        return ConnectorResult(output_json=output, metadata_json=metadata)
