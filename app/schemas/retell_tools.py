from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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