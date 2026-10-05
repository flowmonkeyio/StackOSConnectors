"""Build only the fixed operation's native Slack parameters."""

from typing import Any

from stackos_connectors.contracts import ConnectorRequest

FIELDS = {
    "identity.get": (),
    "message.send": (
        "channel",
        "text",
        "blocks",
        "thread_ts",
        "reply_broadcast",
        "unfurl_links",
        "unfurl_media",
    ),
    "reaction.add": ("channel", "timestamp", "name"),
    "message.delete": ("channel", "ts"),
    "conversation.open": ("channel", "users", "return_im"),
    "conversation.info": ("channel", "include_num_members"),
    "conversation.list": ("limit", "exclude_archived", "cursor", "team_id", "types"),
    "conversation.members": ("channel", "limit", "cursor"),
    "conversation.history": ("channel", "limit", "cursor", "latest", "oldest", "inclusive"),
}


def request_payload(request: ConnectorRequest) -> dict[str, Any]:
    payload = {
        key: request.input_json[key]
        for key in FIELDS[request.operation]
        if key in request.input_json
    }
    if isinstance(payload.get("users"), list):
        payload["users"] = ",".join(payload["users"])
    if isinstance(payload.get("types"), list):
        payload["types"] = ",".join(payload["types"])
    return payload
