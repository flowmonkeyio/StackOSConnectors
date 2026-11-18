"""Native ElevenLabs action dispatch. The catalog validates action inputs."""

from contextlib import nullcontext

import httpx

from stackos_connectors.contracts import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import credential_value
from stackos_connectors.shared.vendor_utils import result

from .integration import ElevenLabsIntegration


class ElevenLabsActionConnector:
    key = "elevenlabs"

    def validate(self, _request: ConnectorRequest) -> list[ValidationIssue]:
        return []

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        options = request.options
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
                case _:
                    raise ValidationError("unsupported ElevenLabs operation")
        return result(self.key, request.operation, output, 0.0)
