from __future__ import annotations

from typing import Any


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


RETELL_DISABLED_CODE = "retell_disabled"
RETELL_SIGNATURE_MISSING_CODE = "retell_signature_missing"
RETELL_SIGNATURE_INVALID_CODE = "retell_signature_invalid"
RETELL_WEBHOOK_VERIFICATION_UNAVAILABLE_CODE = "retell_webhook_verification_unavailable"
RETELL_PAYLOAD_TOO_LARGE_CODE = "retell_payload_too_large"
INVALID_RETELL_PAYLOAD_CODE = "invalid_retell_payload"
UNSUPPORTED_RETELL_TOOL_CODE = "unsupported_retell_tool"
RETELL_TOOL_PROVIDER_CALL_ID_REQUIRED_CODE = "retell_tool_provider_call_id_required"
RETELL_TOOL_ARGUMENTS_INVALID_CODE = "retell_tool_arguments_invalid"


def error_detail(
    *,
    code: str,
    message: str,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
    }