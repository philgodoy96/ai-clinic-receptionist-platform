from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus


class SpecialtyResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class DoctorResponse(BaseModel):
    id: UUID
    specialty_id: UUID
    full_name: str
    email: str | None
    phone_number: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AvailabilitySlotResponse(BaseModel):
    id: UUID
    doctor_id: UUID
    start_time: datetime
    end_time: datetime
    status: AvailabilitySlotStatus

    model_config = ConfigDict(from_attributes=True)


class PatientLookupRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    date_of_birth: date
    phone_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)


class PatientResponse(BaseModel):
    id: UUID
    full_name: str
    date_of_birth: date
    phone_number: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class UpcomingAppointmentsRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    date_of_birth: date
    phone_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    start_from: datetime


class AppointmentResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    specialty_id: UUID
    availability_slot_id: UUID | None
    rescheduled_from_appointment_id: UUID | None
    start_time: datetime
    end_time: datetime
    status: AppointmentStatus
    reason: str | None
    cancellation_reason: str | None
    cancelled_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AppointmentHoldCreateRequest(BaseModel):
    availability_slot_id: UUID
    owner_id: str = Field(min_length=1, max_length=120)


class AppointmentHoldResponse(BaseModel):
    hold_id: UUID
    availability_slot_id: UUID
    doctor_id: UUID
    start_time: datetime
    end_time: datetime
    owner_id: str
    expires_in_seconds: int


class AppointmentBookingRequestBody(BaseModel):
    hold_id: UUID
    availability_slot_id: UUID
    patient_id: UUID
    owner_id: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)