from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, Doctor, Patient
from app.repositories.scheduling import (
    AppointmentRepository,
    AvailabilitySlotRepository,
    DoctorRepository,
    PatientRepository,
)
from app.services.appointment_holds import AppointmentHoldService


class AppointmentBookingServiceError(Exception):
    """Base exception for appointment booking service errors."""


class AppointmentBookingOwnerRequiredError(AppointmentBookingServiceError):
    """Raised when the booking request does not include a hold owner."""


class BookingPatientNotFoundError(AppointmentBookingServiceError):
    """Raised when the requested patient does not exist."""


class BookingDoctorNotFoundError(AppointmentBookingServiceError):
    """Raised when the slot doctor does not exist or is inactive."""


class BookingAvailabilitySlotNotFoundError(AppointmentBookingServiceError):
    """Raised when the availability slot does not exist."""


class BookingAvailabilitySlotUnavailableError(AppointmentBookingServiceError):
    """Raised when the availability slot is not available for booking."""


class AppointmentSlotAlreadyBookedError(AppointmentBookingServiceError):
    """Raised when the doctor/start_time already has a scheduled appointment."""


@dataclass(frozen=True, slots=True)
class AppointmentBookingRequest:
    hold_id: UUID
    availability_slot_id: UUID
    patient_id: UUID
    owner_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentBookingResult:
    appointment: Appointment
    hold: AppointmentHold
    patient: Patient
    doctor: Doctor


class AppointmentBookingService:
    def __init__(
        self,
        *,
        patients: PatientRepository,
        doctors: DoctorRepository,
        availability_slots: AvailabilitySlotRepository,
        appointments: AppointmentRepository,
        hold_service: AppointmentHoldService,
    ) -> None:
        self.patients = patients
        self.doctors = doctors
        self.availability_slots = availability_slots
        self.appointments = appointments
        self.hold_service = hold_service

    def book_appointment(
        self,
        request: AppointmentBookingRequest,
    ) -> AppointmentBookingResult:
        owner_id = request.owner_id.strip()

        if not owner_id:
            raise AppointmentBookingOwnerRequiredError("owner_id is required")

        patient = self.patients.get_by_id(request.patient_id)

        if patient is None:
            raise BookingPatientNotFoundError("patient was not found")

        slot = self.availability_slots.get_by_id(request.availability_slot_id)

        if slot is None:
            raise BookingAvailabilitySlotNotFoundError("availability slot was not found")

        if slot.status != AvailabilitySlotStatus.AVAILABLE:
            raise BookingAvailabilitySlotUnavailableError(
                "availability slot is not available",
            )

        doctor = self.doctors.get_by_id(slot.doctor_id)

        if doctor is None or not doctor.is_active:
            raise BookingDoctorNotFoundError("doctor was not found or is inactive")

        hold = self.hold_service.validate_hold(
            hold_id=request.hold_id,
            doctor_id=slot.doctor_id,
            start_time=slot.start_time,
            owner_id=owner_id,
        )

        existing_appointment = self.appointments.find_scheduled_conflict(
            doctor_id=slot.doctor_id,
            start_time=slot.start_time,
        )

        if existing_appointment is not None:
            raise AppointmentSlotAlreadyBookedError(
                "doctor already has a scheduled appointment at this time",
            )

        appointment = Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            specialty_id=doctor.specialty_id,
            availability_slot_id=slot.id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status=AppointmentStatus.SCHEDULED,
            reason=self._normalize_reason(request.reason),
        )

        slot.status = AvailabilitySlotStatus.BOOKED

        appointment = self.appointments.add(appointment)

        return AppointmentBookingResult(
            appointment=appointment,
            hold=hold,
            patient=patient,
            doctor=doctor,
        )

    def _normalize_reason(self, reason: str | None) -> str | None:
        if reason is None:
            return None

        normalized = reason.strip()

        if not normalized:
            return None

        return normalized
