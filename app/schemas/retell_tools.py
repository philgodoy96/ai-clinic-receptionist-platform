from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.patient_identity_matching import sanitize_spoken_email
from app.domain.scheduling.expressions import DateExpressionKind, TimeWindowExpressionKind
from app.schemas.scheduling_expressions import (
    DateExpressionSchema,
    TimeWindowExpressionSchema,
)

MIN_CHECK_AVAILABILITY_LIMIT = 1
MAX_CHECK_AVAILABILITY_LIMIT = 50
MIN_HOLD_TTL_SECONDS = 60
MAX_HOLD_TTL_SECONDS = 900
MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH = 500
MAX_BOOK_APPOINTMENT_NOTES_LENGTH = 500
MAX_CANCEL_APPOINTMENT_CONFIRMATION_TEXT_LENGTH = 500
MAX_CANCEL_APPOINTMENT_CANCELLATION_REASON_LENGTH = 500
MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH = 500
MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH = 500
MAX_PATIENT_IDENTITY_CONFIRMATION_TEXT_LENGTH = 500
DEFAULT_LIST_PATIENT_APPOINTMENTS_LIMIT = 5
MIN_LIST_PATIENT_APPOINTMENTS_LIMIT = 1
MAX_LIST_PATIENT_APPOINTMENTS_LIMIT = 10
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


class GetClinicContextToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckAvailabilityToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctor_id: UUID | None = None
    doctor_name: str | None = Field(default=None, max_length=160)
    specialty_name: str | None = Field(default=None, max_length=120)
    date_expression: DateExpressionSchema | None = None
    time_window_expression: TimeWindowExpressionSchema | None = None
    requested_date_text: str | None = Field(default=None, max_length=120)
    start_from: datetime | None = None
    start_to: datetime | None = None
    limit: int | None = Field(
        default=None,
        ge=MIN_CHECK_AVAILABILITY_LIMIT,
        le=MAX_CHECK_AVAILABILITY_LIMIT,
    )

    @field_validator("date_expression")
    @classmethod
    def validate_date_expression_fields(
        cls,
        value: DateExpressionSchema | None,
    ) -> DateExpressionSchema | None:
        if value is None:
            return None

        if (
            value.kind
            in {
                DateExpressionKind.THIS_WEEKDAY,
                DateExpressionKind.NEXT_WEEKDAY,
            }
            and value.weekday is None
        ):
            msg = "weekday is required for weekday date expressions"
            raise ValueError(msg)

        if value.kind is DateExpressionKind.IN_N_DAYS and value.days_offset is None:
            msg = "days_offset is required for in_n_days date expressions"
            raise ValueError(msg)

        if value.kind is DateExpressionKind.EXACT_DATE and value.exact_date is None:
            msg = "exact_date is required for exact_date expressions"
            raise ValueError(msg)

        return value

    @field_validator("time_window_expression")
    @classmethod
    def validate_time_window_expression_fields(
        cls,
        value: TimeWindowExpressionSchema | None,
    ) -> TimeWindowExpressionSchema | None:
        if value is None:
            return None

        if (
            value.kind is TimeWindowExpressionKind.EXACT_TIME
            and not (value.exact_time or "").strip()
        ):
            msg = "exact_time is required for exact_time window expressions"
            raise ValueError(msg)

        return value

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
    patient_resolution_id: str | None = Field(default=None, max_length=120)
    explicit_confirmation: bool
    confirmation_text: str | None = Field(
        default=None,
        max_length=MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_BOOK_APPOINTMENT_NOTES_LENGTH,
    )

    @field_validator("patient_email", mode="before")
    @classmethod
    def sanitize_patient_email_before_validation(cls, value: object) -> object:
        if value is None:
            return None

        return sanitize_spoken_email(str(value))

    @field_validator("patient_name")
    @classmethod
    def validate_patient_name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "patient_name cannot be blank"
            raise ValueError(msg)
        return stripped

    @field_validator("patient_resolution_id")
    @classmethod
    def validate_patient_resolution_id_not_blank_if_present(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            msg = "patient_resolution_id cannot be blank"
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
    patient_resolution_id: str | None = Field(default=None, max_length=120)
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

    @field_validator("patient_email", mode="before")
    @classmethod
    def sanitize_patient_email_before_validation(cls, value: object) -> object:
        if value is None:
            return None

        return sanitize_spoken_email(str(value))

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

    @field_validator("patient_resolution_id")
    @classmethod
    def validate_patient_resolution_id_not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            msg = "patient_resolution_id cannot be blank"
            raise ValueError(msg)

        return stripped

    @field_validator("confirmation_text")
    @classmethod
    def validate_confirmation_text_not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            msg = "confirmation_text cannot be blank"
            raise ValueError(msg)

        return stripped


class RescheduleAppointmentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_appointment_id: UUID | str | None = None
    hold_id: str | None = Field(default=None, max_length=120)
    new_slot_id: UUID | str | None = None
    explicit_confirmation: bool
    confirmation_text: str | None = Field(
        default=None,
        max_length=MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    )
    reschedule_reason: str | None = Field(
        default=None,
        max_length=MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH,
    )
    patient_name: str | None = Field(default=None, max_length=160)
    patient_date_of_birth: date | None = None
    patient_email: str | None = Field(
        default=None,
        max_length=255,
        pattern=_PATIENT_EMAIL_PATTERN,
    )

    @field_validator("patient_email", mode="before")
    @classmethod
    def sanitize_patient_email_before_validation(cls, value: object) -> object:
        if value is None:
            return None

        return sanitize_spoken_email(str(value))

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

    @model_validator(mode="after")
    def validate_target_reference(self) -> RescheduleAppointmentToolArguments:
        has_hold = self.hold_id is not None and self.hold_id.strip() != ""
        has_slot = self.new_slot_id is not None and str(self.new_slot_id).strip() != ""
        if not has_hold and not has_slot:
            msg = "either hold_id or new_slot_id is required"
            raise ValueError(msg)
        return self


class ResolvePatientIdentityToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_name: str = Field(min_length=1, max_length=160)
    patient_date_of_birth: date
    patient_email: str | None = Field(
        default=None,
        max_length=255,
        pattern=_PATIENT_EMAIL_PATTERN,
    )
    patient_phone: str | None = Field(default=None, max_length=40)
    caller_claims_existing_patient: bool = True
    allow_demo_patient_creation: bool = False

    @field_validator("patient_email", mode="before")
    @classmethod
    def sanitize_patient_email_before_validation(cls, value: object) -> object:
        if value is None:
            return None

        return sanitize_spoken_email(str(value))

    @field_validator("patient_name")
    @classmethod
    def validate_patient_name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "patient_name cannot be blank"
            raise ValueError(msg)

        return stripped


class ConfirmPatientIdentityToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_resolution_id: str = Field(min_length=1, max_length=120)
    confirmed: bool
    confirmation_text: str | None = Field(
        default=None,
        max_length=MAX_PATIENT_IDENTITY_CONFIRMATION_TEXT_LENGTH,
    )

    @field_validator("patient_resolution_id")
    @classmethod
    def validate_patient_resolution_id_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "patient_resolution_id cannot be blank"
            raise ValueError(msg)

        return stripped


class ListPatientAppointmentsToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_resolution_id: str = Field(min_length=1, max_length=120)
    limit: int = Field(
        default=DEFAULT_LIST_PATIENT_APPOINTMENTS_LIMIT,
        ge=MIN_LIST_PATIENT_APPOINTMENTS_LIMIT,
        le=MAX_LIST_PATIENT_APPOINTMENTS_LIMIT,
    )

    @field_validator("patient_resolution_id")
    @classmethod
    def validate_patient_resolution_id_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "patient_resolution_id cannot be blank"
            raise ValueError(msg)

        return stripped
