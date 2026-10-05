"""Slack external upload using caller-owned local files and truthful stage receipts."""

from pathlib import Path

from stackos_connectors.errors import ConnectorError, ValidationError

from .http import slack_api, upload_bytes
from .results import native_result


async def upload_files(request):
    payload = request.input_json
    prepared = []
    for item in payload["files"]:
        path = Path(item["path"])
        if not path.is_file():
            raise ValidationError("Slack upload file does not exist")
        content = path.read_bytes()
        prepared.append((item, content))
    uploaded = []
    reserved = []
    stage = "allocate_upload"
    upload_headers = {}
    try:
        for item, content in prepared:
            stage = "allocate_upload"
            _, allocation, _ = await slack_api(
                request,
                "POST",
                "files.getUploadURLExternal",
                form_body={"filename": item["filename"], "length": str(len(content))},
            )
            file_id = allocation.get("file_id") if isinstance(allocation, dict) else None
            upload_url = allocation.get("upload_url") if isinstance(allocation, dict) else None
            if isinstance(file_id, str) and file_id:
                reserved.append(file_id)
            if (
                not isinstance(file_id, str)
                or not file_id
                or not isinstance(upload_url, str)
                or not upload_url
            ):
                raise ConnectorError(
                    "Slack upload allocation is incomplete",
                    metadata_json={
                        "provider_executed": True,
                        "retry_safe": False,
                        "outcome_unknown": True,
                    },
                )
            stage = "upload_bytes"
            status, upload_headers = await upload_bytes(
                request,
                upload_url=upload_url,
                content=content,
                mime_type=item.get("mime_type") or "application/octet-stream",
            )
            uploaded.append(
                {
                    "id": file_id,
                    "title": item.get("title") or item["filename"],
                    "filename": item["filename"],
                    "mime_type": item.get("mime_type") or "application/octet-stream",
                    "size_bytes": len(content),
                    "upload_status_code": status,
                }
            )
        stage = "complete_upload"
        complete = {
            "files": [{"id": item["id"], "title": item["title"]} for item in uploaded],
            "channel_id": payload["channel"],
        }
        for key in ("initial_comment", "thread_ts"):
            if payload.get(key):
                complete[key] = payload[key]
        status, body, headers = await slack_api(
            request, "POST", "files.completeUploadExternal", json_body=complete
        )
        result = native_result(
            request,
            status,
            body,
            headers,
            {**complete, "channel": payload["channel"], "files": uploaded},
            "files.completeUploadExternal",
        )
        result.output_json["upload_headers"] = dict(upload_headers)
        return result
    except ConnectorError as exc:
        exc.output_json = {
            **exc.output_json,
            "stage": stage,
            "reserved_file_ids": reserved,
            "uploaded_files": uploaded,
            "channel": payload["channel"],
            "completed": False,
        }
        exc.metadata_json.update({"provider_executed": True, "retry_safe": False, "stage": stage})
        raise
    except Exception as exc:
        raise ConnectorError(
            "Slack upload could not complete",
            output_json={
                "stage": stage,
                "reserved_file_ids": reserved,
                "uploaded_files": uploaded,
                "channel": payload["channel"],
                "completed": False,
            },
            metadata_json={"provider_executed": True, "retry_safe": False, "outcome_unknown": True},
        ) from exc
