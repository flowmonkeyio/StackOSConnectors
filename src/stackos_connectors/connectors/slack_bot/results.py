"""Provider receipts without caller references or communication state."""

from stackos_connectors.contracts import ConnectorResult


def native_result(request, status, body, headers, sent_payload, route):
    return ConnectorResult(
        output_json={
            "data": body,
            "sent_payload": sent_payload,
            "status_code": status,
            "headers": dict(headers),
        },
        metadata_json={
            "vendor": "slack-bot",
            "operation": request.operation,
            "slack_method": route,
            "status_code": status,
            "request_id": headers.get("x-slack-req-id"),
            "provider_executed": True,
            "retry_safe": request.operation
            in {
                "identity.get",
                "conversation.info",
                "conversation.list",
                "conversation.members",
                "conversation.history",
            },
        },
    )
