"""Outreach API action connector.

Official docs verified:
- Sequence State API: https://developers.outreach.io/api/reference/tag/Sequence-State/
- Making requests / JSON:API media type: https://developers.outreach.io/api/making-requests/
- OAuth: https://developers.outreach.io/api/oauth/
"""

from __future__ import annotations

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    config_str,
    credential_value,
    required_str,
    result,
    send_json,
    unknown_operation,
)


def _base_url(request: ConnectorRequest) -> str:
    return (config_str(request, "base_url", default="https://api.outreach.io") or "").rstrip("/")


def _headers(request: ConnectorRequest) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential_value(request, 'access_token', 'token')}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }


class OutreachActionConnector:
    """Decision-free adapter for Outreach JSON:API endpoints."""

    key = "outreach"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        # Validate native numeric IDs without changing their execution values.
        payload = dict(request.input_json)
        for key in ("sequence_id", "prospect_id", "mailbox_id"):
            if isinstance(payload.get(key), int) and not isinstance(payload[key], bool):
                payload[key] = str(payload[key])
        issues: list[ValidationIssue] = []
        if request.operation != "sequence_state.create":
            return unknown_operation(request)
        required_str(payload, "sequence_id", issues)
        required_str(payload, "prospect_id", issues)
        required_str(payload, "mailbox_id", issues)
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation != "sequence_state.create":
            raise ValidationError(f"unsupported Outreach operation {request.operation!r}")
        payload = request.input_json
        body = {
            "data": {
                "type": "sequenceState",
                "relationships": {
                    "sequence": {
                        "data": {
                            "type": "sequence",
                            "id": str(payload["sequence_id"]),
                        }
                    },
                    "prospect": {
                        "data": {
                            "type": "prospect",
                            "id": str(payload["prospect_id"]),
                        }
                    },
                    "mailbox": {
                        "data": {
                            "type": "mailbox",
                            "id": str(payload["mailbox_id"]),
                        }
                    },
                },
            }
        }
        status, response_body, headers = await send_json(
            http=request.options.http,
            timeout_s=request.options.timeout if request.options.timeout is not None else 60.0,
            method="POST",
            url=f"{_base_url(request)}/api/v2/sequenceStates",
            headers=_headers(request),
            json_body=body,
        )
        return result(
            provider="outreach",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=headers,
        )


__all__ = ["OutreachActionConnector"]
