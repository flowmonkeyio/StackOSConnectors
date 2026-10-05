"""Provider errors independent of any transport adapter or host application."""

from typing import Any


class ConnectorError(Exception):
    def __init__(
        self,
        detail: str,
        *,
        provider_status_code: int | None = None,
        provider_error: Any = None,
        output_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.provider_status_code = provider_status_code
        self.provider_error = provider_error
        if output_json is None:
            output_json = {"status": "failed"}
            if provider_status_code is not None:
                output_json["provider_status_code"] = provider_status_code
            if provider_error is not None:
                output_json["provider_error"] = provider_error
        self.output_json = output_json
        self.metadata_json = metadata_json or {}


class ValidationError(ConnectorError):
    def __init__(
        self,
        detail: str,
        *,
        issues: list[Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.issues = issues or []
        self.data = dict(data or {})
        super().__init__(
            detail,
            metadata_json={
                **({"data": self.data} if self.data else {}),
                "issues": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in self.issues
                ],
                "provider_executed": False,
            },
        )


class IntegrationDownError(Exception):
    def __init__(self, detail: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.data = data or {}


class RateLimitedError(IntegrationDownError):
    pass
