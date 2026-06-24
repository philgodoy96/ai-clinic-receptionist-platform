from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.schemas.retell_tools import (
    BookAppointmentToolArguments,
    CancelAppointmentToolArguments,
    CheckAvailabilityToolArguments,
    ConfirmPatientIdentityToolArguments,
    GetClinicContextToolArguments,
    HoldAppointmentSlotToolArguments,
    ReleaseAppointmentHoldToolArguments,
    RescheduleAppointmentToolArguments,
    ResolvePatientIdentityToolArguments,
    RetellProviderToolCallRequest,
    RetellToolCallResponse,
)

_ARGUMENT_MODEL_BY_TOOL: dict[
    RetellSupportedToolName,
    type[
        GetClinicContextToolArguments
        | CheckAvailabilityToolArguments
        | GetClinicContextToolArguments
        | HoldAppointmentSlotToolArguments
        | ReleaseAppointmentHoldToolArguments
        | BookAppointmentToolArguments
        | CancelAppointmentToolArguments
        | RescheduleAppointmentToolArguments
        | ResolvePatientIdentityToolArguments
        | ConfirmPatientIdentityToolArguments
    ],
] = {}


class RetellSupportedToolName(StrEnum):
    GET_CLINIC_CONTEXT = "get_clinic_context"
    CHECK_AVAILABILITY = "check_availability"
    HOLD_APPOINTMENT_SLOT = "hold_appointment_slot"
    RELEASE_APPOINTMENT_HOLD = "release_appointment_hold"
    BOOK_APPOINTMENT = "book_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    RESOLVE_PATIENT_IDENTITY = "resolve_patient_identity"
    CONFIRM_PATIENT_IDENTITY = "confirm_patient_identity"


class RetellToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class RetellToolCallValidationError(ValueError):
    """Raised when a Retell tool call fails schema or domain validation."""


class MissingProviderCallIdError(RetellToolCallValidationError):
    """Raised when a provider tool call omits provider_call_id."""


class UnsupportedRetellToolNameError(RetellToolCallValidationError):
    """Raised when a provider tool call names an unsupported tool."""


RetellToolArguments = (
    GetClinicContextToolArguments
    | CheckAvailabilityToolArguments
    | HoldAppointmentSlotToolArguments
    | ReleaseAppointmentHoldToolArguments
    | BookAppointmentToolArguments
    | CancelAppointmentToolArguments
    | RescheduleAppointmentToolArguments
    | ResolvePatientIdentityToolArguments
    | ConfirmPatientIdentityToolArguments
)


@dataclass(frozen=True, slots=True)
class ParsedRetellToolCall:
    provider_call_id: str
    tool_call_id: str | None
    tool_name: RetellSupportedToolName
    arguments: RetellToolArguments
    occurred_at: datetime | None


def is_supported_retell_tool_name(tool_name: str) -> bool:
    normalized = tool_name.strip().lower()
    return normalized in _SUPPORTED_TOOL_NAME_VALUES


def normalize_retell_tool_name(tool_name: str) -> RetellSupportedToolName | None:
    normalized = tool_name.strip().lower()
    try:
        return RetellSupportedToolName(normalized)
    except ValueError:
        return None


def parse_retell_tool_call(request: RetellProviderToolCallRequest) -> ParsedRetellToolCall:
    provider_call_id = request.provider_call_id.strip()
    if not provider_call_id:
        msg = "provider_call_id is required"
        raise MissingProviderCallIdError(msg)

    supported_tool_name = normalize_retell_tool_name(request.tool_name)
    if supported_tool_name is None:
        msg = f"unsupported tool name: {request.tool_name}"
        raise UnsupportedRetellToolNameError(msg)

    arguments = _parse_tool_arguments(supported_tool_name, request.arguments)

    return ParsedRetellToolCall(
        provider_call_id=provider_call_id,
        tool_call_id=request.tool_call_id,
        tool_name=supported_tool_name,
        arguments=arguments,
        occurred_at=request.occurred_at,
    )


def build_rejected_tool_call_response(
    *,
    tool_name: str,
    tool_call_id: str | None,
    error_code: str,
    result: dict[str, Any] | None = None,
) -> RetellToolCallResponse:
    return RetellToolCallResponse(
        status=RetellToolCallStatus.REJECTED,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        result=result or {},
        error_code=error_code,
    )


def build_failed_tool_call_response(
    *,
    tool_name: str,
    tool_call_id: str | None,
    error_code: str,
    result: dict[str, Any] | None = None,
) -> RetellToolCallResponse:
    return RetellToolCallResponse(
        status=RetellToolCallStatus.FAILED,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        result=result or {},
        error_code=error_code,
    )


def build_succeeded_tool_call_response(
    *,
    tool_name: str,
    tool_call_id: str | None,
    result: dict[str, Any],
    duplicate: bool = False,
) -> RetellToolCallResponse:
    return RetellToolCallResponse(
        status=RetellToolCallStatus.SUCCEEDED,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        result=result,
        duplicate=duplicate,
    )


def build_retell_tool_call_idempotency_key(
    *,
    provider: str,
    provider_call_id: str,
    tool_name: str,
    tool_call_id: str,
) -> str:
    return f"{provider}:{provider_call_id}:tool:{tool_name}:{tool_call_id}"


def build_retell_tool_call_event_type(tool_name: RetellSupportedToolName) -> str:
    return f"retell_tool:{tool_name.value}"


def serialize_tool_call_outcome(response: RetellToolCallResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")


def deserialize_tool_call_outcome(payload: dict[str, Any]) -> RetellToolCallResponse:
    return RetellToolCallResponse.model_validate(payload)


def _parse_tool_arguments(
    tool_name: RetellSupportedToolName,
    arguments: dict[str, Any],
) -> RetellToolArguments:
    argument_model = _ARGUMENT_MODEL_BY_TOOL[tool_name]

    try:
        return argument_model.model_validate(arguments)
    except ValidationError as exc:
        msg = "tool arguments are invalid"
        raise RetellToolCallValidationError(msg) from exc


_SUPPORTED_TOOL_NAME_VALUES = frozenset(item.value for item in RetellSupportedToolName)

_ARGUMENT_MODEL_BY_TOOL.update(
    {
        RetellSupportedToolName.GET_CLINIC_CONTEXT: GetClinicContextToolArguments,
        RetellSupportedToolName.CHECK_AVAILABILITY: CheckAvailabilityToolArguments,
        RetellSupportedToolName.HOLD_APPOINTMENT_SLOT: HoldAppointmentSlotToolArguments,
        RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD: ReleaseAppointmentHoldToolArguments,
        RetellSupportedToolName.BOOK_APPOINTMENT: BookAppointmentToolArguments,
        RetellSupportedToolName.CANCEL_APPOINTMENT: CancelAppointmentToolArguments,
        RetellSupportedToolName.RESCHEDULE_APPOINTMENT: RescheduleAppointmentToolArguments,
        RetellSupportedToolName.RESOLVE_PATIENT_IDENTITY: ResolvePatientIdentityToolArguments,
        RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY: ConfirmPatientIdentityToolArguments,
    },
)
