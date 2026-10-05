"""Microsoft Graph action connector.

Official docs verified:
- Send mail: https://learn.microsoft.com/graph/api/user-sendmail
- Create event: https://learn.microsoft.com/graph/api/calendar-post-events
- Auth concepts: https://learn.microsoft.com/graph/auth/auth-concepts
- Throttling: https://learn.microsoft.com/graph/throttling
"""

from __future__ import annotations

from stackos_connectors.contracts import (
    ConnectorRequest,
    ConnectorResult,
    ValidationIssue,
)
from stackos_connectors.errors import ValidationError
from stackos_connectors.shared.provider_utils import (
    bearer_headers,
    credential_config,
    dict_field,
    issue,
    q,
    result,
    send_json,
    unknown_operation,
)

_BASE_URL = "https://graph.microsoft.com/v1.0"


def _user_path(request: ConnectorRequest) -> str:
    user_id = request.input_json.get("user_id")
    if not user_id:
        if credential_config(request).get("auth_mode") == "application":
            raise ValidationError("Microsoft Graph application auth requires user_id")
        return "me"
    return f"users/{q(user_id)}"


class MicrosoftGraphActionConnector:
    """Decision-free adapter for Microsoft Graph mail/calendar endpoints."""

    key = "microsoft-365"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        payload = request.input_json
        issues: list[ValidationIssue] = []
        match request.operation:
            case "graph.mail.send":
                dict_field(payload, "message", issues, required=True)
            case "graph.calendar.event.create":
                dict_field(payload, "event", issues, required=True)
            case _:
                issues.extend(unknown_operation(request))
        auth_mode = credential_config(request).get("auth_mode")
        if auth_mode == "application" and not payload.get("user_id"):
            issues.append(
                issue("$.user_id", "user_id is required for application auth", "required")
            )
        return issues

    def estimate_cost_cents(self, _request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        headers = bearer_headers(request, "access_token", "token")
        payload = request.input_json
        match request.operation:
            case "graph.mail.send":
                message = payload.get("message")
                if not isinstance(message, dict):
                    raise ValidationError("Microsoft Graph sendMail requires message object")
                body = {
                    "message": message,
                    "saveToSentItems": bool(payload.get("save_to_sent_items", True)),
                }
                status, response_body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=f"{_BASE_URL}/{_user_path(request)}/sendMail",
                    headers=headers,
                    json_body=body,
                )
            case "graph.calendar.event.create":
                event = payload.get("event")
                if not isinstance(event, dict):
                    raise ValidationError("Microsoft Graph event must be an object")
                calendar_id = payload.get("calendar_id")
                if calendar_id:
                    calendar_id = q(calendar_id)
                    url = f"{_BASE_URL}/{_user_path(request)}/calendars/{calendar_id}/events"
                else:
                    url = f"{_BASE_URL}/{_user_path(request)}/calendar/events"
                status, response_body, response_headers = await send_json(
                    http=request.options.http,
                    timeout_s=request.options.timeout
                    if request.options.timeout is not None
                    else 60.0,
                    method="POST",
                    url=url,
                    headers=headers,
                    json_body=event,
                )
            case _:
                raise ValidationError(
                    f"unsupported Microsoft Graph operation {request.operation!r}"
                )
        return result(
            provider="microsoft-365",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=response_headers,
        )


__all__ = ["MicrosoftGraphActionConnector"]
