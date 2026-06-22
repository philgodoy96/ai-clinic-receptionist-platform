from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.errors import UNSUPPORTED_RETELL_TOOL_CODE
from app.domain.retell_tools import (
    MissingProviderCallIdError,
    ParsedRetellToolCall,
    RetellSupportedToolName,
    UnsupportedRetellToolNameError,
    build_rejected_tool_call_response,
    parse_retell_tool_call,
)
from app.schemas.retell_tools import (
    CheckAvailabilityToolArguments,
    HoldAppointmentSlotToolArguments,
    ReleaseAppointmentHoldToolArguments,
    RetellProviderToolCallRequest,
    RetellToolCallRequest,
)


def test_valid_check_availability_payload_parses() -> None:
    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-123",
            "tool_call_id": "tool-call-1",
            "tool_name": "check_availability",
            "occurred_at": "2026-07-01T09:00:00Z",
            "arguments": {
                "doctor_name": "Dr. Emily Carter",
                "specialty_name": "Dermatology",
                "start_from": "2026-07-01T09:00:00Z",
                "start_to": "2026-07-01T12:00:00Z",
                "limit": 10,
            },
        },
    )

    parsed = parse_retell_tool_call(request)

    assert isinstance(parsed, ParsedRetellToolCall)
    assert parsed.provider_call_id == "retell-call-123"
    assert parsed.tool_call_id == "tool-call-1"
    assert parsed.tool_name is RetellSupportedToolName.CHECK_AVAILABILITY
    assert parsed.occurred_at == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    assert isinstance(parsed.arguments, CheckAvailabilityToolArguments)
    assert parsed.arguments.doctor_name == "Dr. Emily Carter"
    assert parsed.arguments.limit == 10


def test_valid_hold_payload_parses() -> None:
    slot_id = uuid4()

    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-456",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(slot_id),
                "owner_id": "retell-call-456",
                "ttl_seconds": 300,
            },
        },
    )

    parsed = parse_retell_tool_call(request)

    assert parsed.tool_name is RetellSupportedToolName.HOLD_APPOINTMENT_SLOT
    assert isinstance(parsed.arguments, HoldAppointmentSlotToolArguments)
    assert parsed.arguments.availability_slot_id == slot_id
    assert parsed.arguments.ttl_seconds == 300


def test_valid_release_payload_parses() -> None:
    hold_id = uuid4()

    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-789",
            "tool_name": "release_appointment_hold",
            "arguments": {
                "hold_id": str(hold_id),
            },
        },
    )

    parsed = parse_retell_tool_call(request)

    assert parsed.tool_name is RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD
    assert isinstance(parsed.arguments, ReleaseAppointmentHoldToolArguments)
    assert parsed.arguments.hold_id == hold_id


def test_missing_provider_call_id_rejected() -> None:
    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "   ",
            "tool_name": "check_availability",
            "arguments": {
                "start_from": "2026-07-01T09:00:00Z",
                "start_to": "2026-07-01T12:00:00Z",
            },
        },
    )

    with pytest.raises(MissingProviderCallIdError, match="provider_call_id is required"):
        parse_retell_tool_call(request)


def test_unsupported_tool_name_rejected() -> None:
    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-123",
            "tool_name": "cancel_appointment",
            "arguments": {},
        },
    )

    with pytest.raises(UnsupportedRetellToolNameError, match="unsupported tool name"):
        parse_retell_tool_call(request)


def test_unsupported_tool_name_can_be_represented_safely() -> None:
    response = build_rejected_tool_call_response(
        tool_name="book_appointment",
        tool_call_id="tool-call-9",
        error_code=UNSUPPORTED_RETELL_TOOL_CODE,
    )

    assert response.status == "rejected"
    assert response.tool_name == "book_appointment"
    assert response.error_code == UNSUPPORTED_RETELL_TOOL_CODE
    assert response.result == {}


@pytest.mark.parametrize("limit", [0, 51])
def test_invalid_check_availability_limit_rejected(limit: int) -> None:
    with pytest.raises(ValidationError):
        CheckAvailabilityToolArguments.model_validate(
            {
                "start_from": "2026-07-01T09:00:00Z",
                "start_to": "2026-07-01T12:00:00Z",
                "limit": limit,
            },
        )


@pytest.mark.parametrize("ttl_seconds", [30, 901])
def test_invalid_hold_ttl_rejected(ttl_seconds: int) -> None:
    with pytest.raises(ValidationError):
        HoldAppointmentSlotToolArguments.model_validate(
            {
                "availability_slot_id": str(uuid4()),
                "ttl_seconds": ttl_seconds,
            },
        )


def test_schema_does_not_require_phone_numbers() -> None:
    schema = CheckAvailabilityToolArguments.model_json_schema()
    required_fields = schema.get("required", [])

    assert "phone_number" not in required_fields
    assert "phone_number" not in schema.get("properties", {})

    arguments = CheckAvailabilityToolArguments.model_validate(
        {
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert arguments.doctor_name is None
    assert arguments.specialty_name is None


def test_retell_tool_call_request_is_provider_request_alias() -> None:
    assert RetellToolCallRequest is RetellProviderToolCallRequest
