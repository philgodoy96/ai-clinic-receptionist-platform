from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from app.domain.scheduling.availability import (
    NO_MATCHING_SLOTS_RESPONSE_TEXT,
    AvailabilityCheckStatus,
    BookingWindow,
    build_outside_booking_horizon_response_text,
)
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.repositories.scheduling import (
    AppointmentRepository,
    AvailabilitySlotRepository,
    DoctorRepository,
    PatientRepository,
    SpecialtyRepository,
)
from app.services.appointment_holds import (
    AppointmentHoldService,
    AppointmentHoldStoreUnavailableError,
)
from app.services.clinic_time import ClinicTimeService

logger = logging.getLogger(__name__)


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
class SchedulingAvailabilityPolicy:
    min_booking_lead_minutes: int
    booking_horizon_days: int


@dataclass(frozen=True, slots=True)
class AvailabilityCheckResult:
    status: AvailabilityCheckStatus
    available_slots: Sequence[AvailabilitySlot]
    booking_window: BookingWindow | None = None
    suggested_response_text: str | None = None


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
        clinic_time_service: ClinicTimeService | None = None,
        hold_service: AppointmentHoldService | None = None,
        availability_policy: SchedulingAvailabilityPolicy | None = None,
    ) -> None:
        self.specialties = specialties
        self.doctors = doctors
        self.patients = patients
        self.availability_slots = availability_slots
        self.appointments = appointments
        self._clinic_time_service = clinic_time_service
        self._hold_service = hold_service
        self._availability_policy = availability_policy

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
        return self.check_availability_with_status(
            doctor_id=doctor_id,
            start_from=start_from,
            start_to=start_to,
        ).available_slots

    def check_availability_with_status(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> AvailabilityCheckResult:
        if start_to <= start_from:
            raise InvalidAvailabilityWindowError("start_to must be greater than start_from")

        doctor = self.doctors.get_by_id(doctor_id)

        if doctor is None or not doctor.is_active:
            raise DoctorNotFoundError("doctor was not found or is inactive")

        booking_window = self.get_booking_window()
        earliest_bookable, latest_bookable = self._get_booking_bounds()

        normalized_start = _to_utc(start_from)
        normalized_end = _to_utc(start_to)

        if (
            self._availability_policy is not None
            and self._clinic_time_service is not None
            and not _window_overlaps_booking_horizon(
                start_from=normalized_start,
                start_to=normalized_end,
                earliest_bookable=earliest_bookable,
                latest_bookable=latest_bookable,
            )
        ):
            assert booking_window is not None
            return AvailabilityCheckResult(
                status=AvailabilityCheckStatus.OUTSIDE_BOOKING_HORIZON,
                available_slots=[],
                booking_window=booking_window,
                suggested_response_text=build_outside_booking_horizon_response_text(
                    booking_window.latest_bookable_date,
                ),
            )

        query_start, query_end = self._resolve_availability_query_window(
            start_from=start_from,
            start_to=start_to,
        )

        if query_end <= query_start:
            return AvailabilityCheckResult(
                status=AvailabilityCheckStatus.NO_MATCHING_SLOTS,
                available_slots=[],
                booking_window=booking_window,
                suggested_response_text=NO_MATCHING_SLOTS_RESPONSE_TEXT,
            )

        slots = self.availability_slots.list_available(
            doctor_id=doctor_id,
            start_from=query_start,
            start_to=query_end,
        )

        slots = self._apply_availability_policy_filters(slots)

        if self._hold_service is not None and slots:
            slots = self._exclude_redis_held_slots(
                doctor_id=doctor_id,
                slots=slots,
            )

        if slots:
            return AvailabilityCheckResult(
                status=AvailabilityCheckStatus.AVAILABLE,
                available_slots=slots,
                booking_window=booking_window,
            )

        return AvailabilityCheckResult(
            status=AvailabilityCheckStatus.NO_MATCHING_SLOTS,
            available_slots=[],
            booking_window=booking_window,
            suggested_response_text=NO_MATCHING_SLOTS_RESPONSE_TEXT,
        )

    def get_booking_window(self) -> BookingWindow | None:
        if self._availability_policy is None or self._clinic_time_service is None:
            return None

        earliest_bookable, latest_bookable = self._get_booking_bounds()
        timezone_name = str(self._clinic_time_service.timezone.key)

        return BookingWindow(
            earliest_bookable_date=earliest_bookable.astimezone(
                self._clinic_time_service.timezone,
            ).date(),
            latest_bookable_date=latest_bookable.astimezone(
                self._clinic_time_service.timezone,
            ).date(),
            timezone=timezone_name,
        )

    def _get_booking_bounds(self) -> tuple[datetime, datetime]:
        if self._availability_policy is None or self._clinic_time_service is None:
            raise RuntimeError("booking bounds require clinic time and availability policy")

        clinic_now = self._clinic_time_service.clinic_now()
        earliest_bookable = _to_utc(
            clinic_now + timedelta(minutes=self._availability_policy.min_booking_lead_minutes),
        )
        latest_bookable = _to_utc(
            clinic_now + timedelta(days=self._availability_policy.booking_horizon_days),
        )
        return earliest_bookable, latest_bookable

    def _resolve_availability_query_window(
        self,
        *,
        start_from: datetime,
        start_to: datetime,
    ) -> tuple[datetime, datetime]:
        normalized_start = _to_utc(start_from)
        normalized_end = _to_utc(start_to)

        if self._availability_policy is None or self._clinic_time_service is None:
            return normalized_start, normalized_end

        clinic_now = self._clinic_time_service.clinic_now()
        earliest_bookable = clinic_now + timedelta(
            minutes=self._availability_policy.min_booking_lead_minutes,
        )
        latest_bookable = clinic_now + timedelta(
            days=self._availability_policy.booking_horizon_days,
        )

        effective_start = max(normalized_start, _to_utc(earliest_bookable))
        effective_end = min(normalized_end, _to_utc(latest_bookable))

        return effective_start, effective_end

    def _apply_availability_policy_filters(
        self,
        slots: Sequence[AvailabilitySlot],
    ) -> list[AvailabilitySlot]:
        if self._availability_policy is None or self._clinic_time_service is None:
            return list(slots)

        clinic_now = self._clinic_time_service.clinic_now()
        earliest_bookable = _to_utc(
            clinic_now + timedelta(minutes=self._availability_policy.min_booking_lead_minutes),
        )
        latest_bookable = _to_utc(
            clinic_now + timedelta(days=self._availability_policy.booking_horizon_days),
        )

        return [
            slot
            for slot in slots
            if earliest_bookable <= _to_utc(slot.start_time) < latest_bookable
        ]

    def _exclude_redis_held_slots(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[AvailabilitySlot],
    ) -> list[AvailabilitySlot]:
        if self._hold_service is None:
            return list(slots)

        slot_keys = [(slot.id, slot.start_time) for slot in slots]

        try:
            held_slot_ids = self._hold_service.find_held_availability_slot_ids(
                doctor_id=doctor_id,
                slots=slot_keys,
            )
        except AppointmentHoldStoreUnavailableError:
            logger.warning(
                "Skipping Redis hold filtering during availability lookup",
                extra={
                    "doctor_id": str(doctor_id),
                    "candidate_slot_count": len(slots),
                },
            )
            return list(slots)

        return [slot for slot in slots if slot.id not in held_slot_ids]

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


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _window_overlaps_booking_horizon(
    *,
    start_from: datetime,
    start_to: datetime,
    earliest_bookable: datetime,
    latest_bookable: datetime,
) -> bool:
    return start_from < latest_bookable and start_to > earliest_bookable
