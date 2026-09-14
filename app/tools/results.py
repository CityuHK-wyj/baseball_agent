"""Stable tool-result contract for the execution layer.

Separates SUCCESS / NO_DATA / POLICY_REJECTED / TECHNICAL_FAILURE. Zero rows is a
business outcome, not a failure. Any value that reaches a caller must already be
redacted: no credential, connection string or password may appear in a summary.
"""

import re
from typing import Literal

from pydantic import Field

from app.models.artifacts import Artifact, ArtifactContract

ToolStatus = Literal["OK", "EMPTY", "ERROR"]
ToolErrorType = Literal["NONE", "POLICY_REJECTED", "NO_DATA", "TECHNICAL_FAILURE"]

_URL_CREDENTIALS = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+):[^\s@]+@")
_CREDENTIAL_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api_?key)\b\s*[=:]\s*('[^']*'|\"[^\"]*\"|\S+)")


def redact_secrets(text: str, secrets: tuple[str, ...] | list[str] = ()) -> str:
    """Replace known values and credential-shaped substrings with SECRET_REDACTED."""
    redacted = str(text)
    for value in sorted({str(item) for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(value, "SECRET_REDACTED")
    redacted = _URL_CREDENTIALS.sub(r"\1:SECRET_REDACTED@", redacted)
    redacted = _CREDENTIAL_KV.sub(lambda match: f"{match.group(1)}=SECRET_REDACTED", redacted)
    return redacted


class ToolResult(ArtifactContract):
    status: ToolStatus = "OK"
    artifact: Artifact | None = None
    payload: bytes | None = None
    payload_content_type: str = "application/json"
    error_code: str = ""
    error_type: ToolErrorType = "NONE"
    retryable: bool = False
    policy_blocked: bool = False
    safe_error_summary: str = ""
    row_count: int | None = None
    execution_metadata: tuple[str, ...] = ()

    @classmethod
    def ok(cls, row_count: int, metadata: tuple[str, ...] = (), *,
           artifact: Artifact | None = None, payload: bytes | None = None,
           payload_content_type: str = "application/json") -> "ToolResult":
        return cls(status="OK", error_type="NONE", row_count=row_count, execution_metadata=metadata,
                   artifact=artifact, payload=payload, payload_content_type=payload_content_type)

    @classmethod
    def no_data(cls) -> "ToolResult":
        return cls(status="EMPTY", error_type="NO_DATA", row_count=0)

    @classmethod
    def policy(cls, summary: str) -> "ToolResult":
        return cls(status="ERROR", error_type="POLICY_REJECTED", policy_blocked=True,
                   error_code="BLOCKED_BY_POLICY", safe_error_summary=summary)

    @classmethod
    def failure(cls, summary: str, *, retryable: bool, error_code: str = "") -> "ToolResult":
        return cls(status="ERROR", error_type="TECHNICAL_FAILURE", retryable=retryable,
                   error_code=error_code, safe_error_summary=summary)


class ExecutionMetadata(ArtifactContract):
    """Optional structured diagnostics attached to a successful execution."""

    limits: tuple[str, ...] = Field(default_factory=tuple)
