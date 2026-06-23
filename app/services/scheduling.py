from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.repositories.scheduling import (
    AppointmentRepository,
    AvailabilitySlotRepository,
    DoctorRepository,
    PatientRepository,
    SpecialtyRepository,
)


class SchedulingServiceError(Exception):
    """Base exception for scheduling application service errors."""


class InvalidAvailabilityWindowError(SchedulingServiceError):
    """Raised when an availability lookup window is invalid."""


class DoctorNotFoundError(SchedulingServiceError):
    """Raised when a requested doctor does not exist or is inactive."""


class InsufficientPatientIdentityError(SchedulingServiceError):
    """Raised when patient lookup does not include enough identifying information."""


class AvailabilitySlotNotFoundError(SchedulingServiceError):
    """Raised when an availability slot does not exist."""


class AvailabilitySlotUnavailableError(SchedulingServiceError):
    """Raised when an availability slot cannot be held or booked."""


@dataclass(frozen=True, slots=True)
class PatientLookupCriteria:
    full_name: str
    date_of_birth: date
    phone_number: str | None = None
    email: str | None = None

    def has_sufficient_identifiers(self) -> bool:
        return self.phone_number is not None or self.email is not None


class SchedulingService:
    def __init__(
        self,
        *,
        specialties: SpecialtyRepository,
        doctors: DoctorRepository,
        patients: PatientRepository,
        availability_slots: AvailabilitySlotRepository,
        appointments: AppointmentRepository,
    ) -> None:
        self.specialties = specialties
        self.doctors = doctors
        self.patients = patients
        self.availability_slots = availability_slots
        self.appointments = appointments

    def list_specialties(self) -> Sequence[Specialty]:
        return self.specialties.list_active()

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        return self.doctors.list_active(specialty_id=specialty_id)

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        if start_to <= start_from:
            raise InvalidAvailabilityWindowError("start_to must be greater than start_from")

        doctor = self.doctors.get_by_id(doctor_id)

        if doctor is None or not doctor.is_active:
            raise DoctorNotFoundError("doctor was not found or is inactive")

        return self.availability_slots.list_available(
            doctor_id=doctor_id,
            start_from=start_from,
            start_to=start_to,
        )

    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
        slot = self.availability_slots.get_by_id(availability_slot_id)

        if slot is None:
            raise AvailabilitySlotNotFoundError("availability slot was not found")

        if slot.status != AvailabilitySlotStatus.AVAILABLE:
            raise AvailabilitySlotUnavailableError("availability slot is not available")

        return slot

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        if not criteria.has_sufficient_identifiers():
            raise InsufficientPatientIdentityError(
                "patient lookup requires phone_number or email",
            )

        return self.patients.get_by_identity(
            full_name=criteria.full_name,
            date_of_birth=criteria.date_of_birth,
            phone_number=criteria.phone_number,
            email=criteria.email,
        )

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        patient = self.lookup_patient(criteria)

        if patient is None:
            return []

        return self.appointments.list_upcoming_for_patient(
            patient_id=patient.id,
            start_from=start_from,
        )

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        return self.appointments.find_scheduled_conflict(
            doctor_id=doctor_id,
            start_time=start_time,
        )
