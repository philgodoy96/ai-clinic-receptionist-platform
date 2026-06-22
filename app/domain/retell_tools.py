from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.schemas.retell_tools import (
    CheckAvailabilityToolArguments,
    HoldAppointmentSlotToolArguments,
    ReleaseAppointmentHoldToolArguments,
    RetellProviderToolCallRequest,
    RetellToolCallResponse,
)

_ARGUMENT_MODEL_BY_TOOL: dict[
    RetellSupportedToolName,
    type[
        CheckAvailabilityToolArguments
        | HoldAppointmentSlotToolArguments
        | ReleaseAppointmentHoldToolArguments
    ],
] = {}


class RetellSupportedToolName(StrEnum):
    CHECK_AVAILABILITY = "check_availability"
    HOLD_APPOINTMENT_SLOT = "hold_appointment_slot"
    RELEASE_APPOINTMENT_HOLD = "release_appointment_hold"


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
    CheckAvailabilityToolArguments
    | HoldAppointmentSlotToolArguments
    | ReleaseAppointmentHoldToolArguments
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
        RetellSupportedToolName.CHECK_AVAILABILITY: CheckAvailabilityToolArguments,
        RetellSupportedToolName.HOLD_APPOINTMENT_SLOT: HoldAppointmentSlotToolArguments,
        RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD: ReleaseAppointmentHoldToolArguments,
    },
)
