"""Safe evidence returned by provider credential probes; no lifecycle or custody."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PermissionVerification(BaseModel):
    evidence_source: Literal["oauth_response", "provider_probe", "unavailable"]
    model_config = ConfigDict(extra="forbid")


class AuthMethodProbeContext(BaseModel):
    auth_method_key: str
    permission_verification: PermissionVerification | None = None


class AuthProbeAccountEvidence(BaseModel):
    provider_account_id: str | None = None
    display_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthProbeEvidence(BaseModel):
    grants: list[str] | None = None
    account: AuthProbeAccountEvidence | None = None
