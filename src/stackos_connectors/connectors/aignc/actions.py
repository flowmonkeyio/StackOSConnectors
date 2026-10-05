"""Named AIGNC requests with caller-owned files and resolved bearer credentials."""

from contextlib import nullcontext
from pathlib import Path

import httpx

from stackos_connectors.connectors.aignc.integration import AigncIntegration
from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.shared.provider_utils import credential_value, issue


class AigncActionConnector:
    key = "aignc"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        issues = []
        if request.operation == "image.generate" and request.options.output_dir is None:
            issues.append(
                issue("$.options.output_dir", "output_dir is required for generated media")
            )
        for name in ("max_response_bytes", "max_image_bytes", "max_audio_bytes"):
            value = request.options.provider_context.get(name)
            if value is not None and (type(value) is not int or value <= 0):
                issues.append(
                    issue(
                        f"$.options.provider_context.{name}", f"{name} must be a positive integer"
                    )
                )
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        data = request.input_json
        async with (
            nullcontext(request.options.http)
            if request.options.http is not None
            else httpx.AsyncClient()
        ) as http:
            client = AigncIntegration(
                payload=credential_value(request, "api_key").encode(),
                http=http,
                timeout=request.options.timeout,
                rate_limiter=request.options.rate_limiter,
                output_dir=request.options.output_dir,
                **{
                    name: request.options.provider_context.get(name)
                    for name in ("max_response_bytes", "max_image_bytes", "max_audio_bytes")
                },
            )
            if request.operation == "models.list":
                result = await client.models()
            elif request.operation == "chat.complete":
                result = await client.chat_complete(
                    model=data["model"],
                    messages=data["messages"],
                    max_tokens=data["max_tokens"],
                    google_search=data.get("google_search", False),
                )
            elif request.operation == "image.generate":
                result = await client.generate_image(
                    prompt=data["prompt"], max_tokens=data["max_tokens"]
                )
            elif request.operation == "audio.analyze":
                result = await client.analyze_audio(
                    model=data["model"],
                    instruction=data["instruction"],
                    audio_path=Path(data["audio_path"]),
                    format=data["format"],
                    max_tokens=data["max_tokens"],
                )
            else:
                from stackos_connectors.errors import ValidationError

                raise ValidationError("unsupported AIGNC operation")
        return ConnectorResult(
            output_json=result.data,
            metadata_json={"vendor": "aignc", **(result.metadata or {})},
            files=client.files,
        )
