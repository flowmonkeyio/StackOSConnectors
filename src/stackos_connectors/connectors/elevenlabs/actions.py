"""Native ElevenLabs action dispatch. The catalog validates action inputs."""

from contextlib import nullcontext
from pathlib import Path

import httpx

from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import issue

from .integration import ElevenLabsIntegration


class ElevenLabsActionConnector:
    key = "elevenlabs"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation == "speech.generate" and request.options.output_dir is None:
            return [issue("$.options.output_dir", "output_dir is required for generated speech")]
        return []

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        options = request.options
        files = []
        receipt = {}
        async with (
            nullcontext(options.http)
            if options.http is not None
            else httpx.AsyncClient(timeout=30.0)
        ) as http:
            integration = ElevenLabsIntegration(
                payload=credential_value(request, "api_key").encode(),
                http=http,
                rate_limiter=options.rate_limiter,
                timeout=options.timeout,
            )
            data = request.input_json
            match request.operation:
                case "subscription.get":
                    output = await integration.subscription()
                case "models.list":
                    output = await integration.list_models()
                case "voices.list":
                    output = await integration.list_voices(
                        page_size=int(data.get("page_size", 10)), page_token=data.get("page_token")
                    )
                case "voices.settings.default.get":
                    output = await integration.voice_settings()
                case "voices.settings.get":
                    output = await integration.voice_settings(voice_id=data["voice_id"])
                case "speech.generate":
                    body = {
                        key: value
                        for key, value in data.items()
                        if key not in {"voice_id", "output_format"}
                    }
                    if body.get("seed") is not None:
                        body["seed"] = int(body["seed"])
                    output, receipt = await integration.generate_speech(
                        voice_id=data["voice_id"],
                        body=body,
                        output_format=data.get("output_format"),
                        output_dir=options.output_dir,
                        files=files,
                    )
                case "alignment.create":
                    output, receipt = await integration.create_alignment(
                        audio_path=Path(data["audio_path"]),
                        mime_type=data["mime_type"],
                        text=data["text"],
                    )
                case _:
                    raise ValidationError("unsupported ElevenLabs operation")
        return ConnectorResult(
            output_json=output,
            metadata_json={"vendor": self.key, "operation": request.operation, **receipt},
            files=files,
        )
