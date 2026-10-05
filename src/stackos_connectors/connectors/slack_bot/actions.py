"""Explicit Slack Web API operations with native identifiers and resolved auth."""

from stackos_connectors.contracts import ConnectorRequest, ConnectorResult
from stackos_connectors.errors import ValidationError

from .files import upload_files
from .http import slack_api
from .payloads import request_payload
from .results import native_result
from .validation import validate_request

ROUTES = {
    "identity.get": ("POST", "auth.test"),
    "message.send": ("POST", "chat.postMessage"),
    "reaction.add": ("POST", "reactions.add"),
    "message.delete": ("POST", "chat.delete"),
    "conversation.open": ("POST", "conversations.open"),
    "conversation.info": ("GET", "conversations.info"),
    "conversation.list": ("GET", "conversations.list"),
    "conversation.members": ("GET", "conversations.members"),
    "conversation.history": ("GET", "conversations.history"),
}


class SlackBotActionConnector:
    key = "slack-bot"

    def validate(self, request: ConnectorRequest):
        return validate_request(request)

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        if request.operation == "file.upload":
            return await upload_files(request)
        if request.operation not in ROUTES:
            raise ValidationError("unsupported Slack operation")
        method, route = ROUTES[request.operation]
        payload = request_payload(request)
        status, body, headers = await slack_api(
            request,
            method,
            route,
            **({"params": payload} if method == "GET" else {"json_body": payload or None}),
        )
        return native_result(request, status, body, headers, payload, route)
