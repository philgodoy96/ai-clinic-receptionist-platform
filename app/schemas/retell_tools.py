from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_CHECK_AVAILABILITY_LIMIT = 1
MAX_CHECK_AVAILABILITY_LIMIT = 50
MIN_HOLD_TTL_SECONDS = 60
MAX_HOLD_TTL_SECONDS = 900


class RetellToolRequestContext(BaseModel):
    call_id: str | None = Field(default=None, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=120)


class RetellListSpecialtiesRequest(RetellToolRequestContext):
    pass


class RetellListDoctorsRequest(RetellToolRequestContext):
    specialty_name: str | None = Field(default=None, max_length=120)
    doctor_name: str | None = Field(default=None, max_length=160)


class RetellCheckAvailabilityRequest(RetellToolRequestContext):
    doctor_id: UUID | None = None
    doctor_name: str | None = Field(default=None, max_length=160)
    specialty_name: str | None = Field(default=None, max_length=120)
    start_from: datetime
    start_to: datetime


class RetellPatientLookupRequest(RetellToolRequestContext):
    full_name: str = Field(min_length=1, max_length=160)
    date_of_birth: date
    phone_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)


class RetellUpcomingAppointmentsRequest(RetellPatientLookupRequest):
    start_from: datetime


class RetellHoldAppointmentSlotRequest(RetellToolRequestContext):
    availability_slot_id: UUID
    owner_id: str | None = Field(default=None, max_length=120)


class RetellBookAppointmentRequest(RetellToolRequestContext):
    hold_id: UUID
    availability_slot_id: UUID
    patient_id: UUID
    owner_id: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class RetellToolResponse(BaseModel):
    ok: bool
    result: dict[str, Any] | list[dict[str, Any]] | None = None
    error_code: str | None = None
    message: str | None = None


class RetellProviderToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider_call_id: str = Field(min_length=1, max_length=120)
    tool_call_id: str | None = Field(default=None, max_length=120)
    tool_name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


RetellToolCallRequest = RetellProviderToolCallRequest


class RetellToolCallResponse(BaseModel):
    status: Literal["succeeded", "failed", "rejected"]
    tool_name: str = Field(min_length=1, max_length=120)
    tool_call_id: str | None = Field(default=None, max_length=120)
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=120)
    duplicate: bool = False


class CheckAvailabilityToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctor_id: UUID | None = None
    doctor_name: str | None = Field(default=None, max_length=160)
    specialty_name: str | None = Field(default=None, max_length=120)
    start_from: datetime | None = None
    start_to: datetime | None = None
    limit: int | None = Field(
        default=None,
        ge=MIN_CHECK_AVAILABILITY_LIMIT,
        le=MAX_CHECK_AVAILABILITY_LIMIT,
    )

    @model_validator(mode="after")
    def validate_availability_window(self) -> CheckAvailabilityToolArguments:
        if (
            self.start_from is not None
            and self.start_to is not None
            and self.start_to <= self.start_from
        ):
            msg = "start_to must be greater than start_from"
            raise ValueError(msg)

        return self


class HoldAppointmentSlotToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability_slot_id: UUID
    owner_id: str | None = Field(default=None, max_length=120)
    ttl_seconds: int | None = Field(
        default=None,
        ge=MIN_HOLD_TTL_SECONDS,
        le=MAX_HOLD_TTL_SECONDS,
    )


class ReleaseAppointmentHoldToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hold_id: UUID
    owner_id: str | None = Field(default=None, max_length=120)