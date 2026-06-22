from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_CHECK_AVAILABILITY_LIMIT = 1
MAX_CHECK_AVAILABILITY_LIMIT = 50
MIN_HOLD_TTL_SECONDS = 60
MAX_HOLD_TTL_SECONDS = 900
MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH = 500
MAX_BOOK_APPOINTMENT_NOTES_LENGTH = 500
MAX_CANCEL_APPOINTMENT_CONFIRMATION_TEXT_LENGTH = 500
MAX_CANCEL_APPOINTMENT_CANCELLATION_REASON_LENGTH = 500
_PATIENT_EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


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


class BookAppointmentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hold_id: str | None = Field(default=None, max_length=120)
    slot_id: UUID | str | None = None
    patient_name: str = Field(min_length=1, max_length=160)
    patient_date_of_birth: date
    patient_email: str = Field(min_length=1, max_length=255, pattern=_PATIENT_EMAIL_PATTERN)
    patient_phone: str | None = Field(default=None, max_length=40)
    explicit_confirmation: bool
    confirmation_text: str | None = Field(
        default=None,
        max_length=MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_BOOK_APPOINTMENT_NOTES_LENGTH,
    )

    @field_validator("patient_name")
    @classmethod
    def validate_patient_name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "patient_name cannot be blank"
            raise ValueError(msg)
        return stripped

    @model_validator(mode="after")
    def validate_slot_reference(self) -> BookAppointmentToolArguments:
        has_hold = self.hold_id is not None and self.hold_id.strip() != ""
        has_slot = self.slot_id is not None and str(self.slot_id).strip() != ""
        if not has_hold and not has_slot:
            msg = "either hold_id or slot_id is required"
            raise ValueError(msg)
        return self


class CancelAppointmentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: UUID | str | None = None
    explicit_confirmation: bool
    confirmation_text: str | None = Field(
        default=None,
        max_length=MAX_CANCEL_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    )
    cancellation_reason: str | None = Field(
        default=None,
        max_length=MAX_CANCEL_APPOINTMENT_CANCELLATION_REASON_LENGTH,
    )
    patient_name: str | None = Field(default=None, max_length=160)
    patient_date_of_birth: date | None = None
    patient_email: str | None = Field(
        default=None,
        max_length=255,
        pattern=_PATIENT_EMAIL_PATTERN,
    )

    @field_validator("patient_name")
    @classmethod
    def validate_patient_name_not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            msg = "patient_name cannot be blank"
            raise ValueError(msg)

        return stripped