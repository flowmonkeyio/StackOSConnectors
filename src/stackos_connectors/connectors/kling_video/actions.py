"""Native named media calls; artifact and pricing policy belongs to the caller."""

import json
from contextlib import nullcontext
from pathlib import Path
from typing import ClassVar

import httpx

from stackos_connectors.connectors.kling_video.integration import KlingVideoIntegration
from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError
from stackos_connectors.shared.provider_utils import credential_payload, issue


class MediaActionConnector:
    key = "kling-video"
    _methods: ClassVar[dict[str, str]] = {"video.generate": "generate_video"}

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.options.output_dir is None:
            return [issue("$.options.output_dir", "output_dir is required for generated media")]
        return []

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        data = dict(request.input_json)
        for key, value in tuple(data.items()):
            if key.endswith("_paths") and value is not None:
                data[key] = [Path(item) for item in value]
            elif key.endswith("_path") and value is not None:
                data[key] = Path(value)
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient()
        ) as http:
            client = KlingVideoIntegration(
                payload=json.dumps(credential_payload(request)).encode(),
                http=http,
                timeout=request.options.timeout,
                output_dir=request.options.output_dir,
                rate_limiter=request.options.rate_limiter,
            )
            try:
                result = await getattr(client, self._methods[request.operation])(**data)
            except (IntegrationDownError, OSError, ValueError, ValidationError) as exc:
                details = (
                    exc.data if isinstance(exc, (IntegrationDownError, ValidationError)) else {}
                )
                status = details.get("status")
                status_code = status if type(status) is int else None
                metadata = {
                    **client.receipt,
                    **details,
                    "provider_executed": client.provider_executed,
                    "submission_http_succeeded": client.submission_http_succeeded,
                    "generation_outcome": client.generation_outcome,
                    "retry_safe": not client.provider_executed,
                    "outcome_unknown": client.provider_executed
                    and client.generation_outcome is None
                    and (
                        client.submission_http_succeeded
                        or status_code is None
                        or status_code >= 500
                    ),
                    "files": [file.model_dump() for file in client.files],
                }
                if isinstance(exc, ValidationError) and not client.provider_executed:
                    raise
                raise ConnectorError(
                    str(exc)
                    if not isinstance(exc, OSError)
                    else "generated media could not be written",
                    provider_status_code=status_code,
                    provider_error=details.get("provider_error"),
                    metadata_json=metadata,
                ) from None
        return ConnectorResult(
            output_json=result.data if isinstance(result.data, dict) else {"data": result.data},
            metadata_json={"vendor": self.key, **client.receipt, **(result.metadata or {})},
            files=client.files,
            cost_cents=round(result.cost_usd * 100),
        )
