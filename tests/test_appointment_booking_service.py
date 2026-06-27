from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.patient_identity_matching import is_exact_name_match
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.scheduling.phone import normalize_phone_digits
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.appointment_booking import (
    AppointmentBookingOwnerRequiredError,
    AppointmentBookingRequest,
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
    BookingAvailabilitySlotUnavailableError,
    BookingPatientNotFoundError,
)
from app.services.appointment_holds import AppointmentHoldNotFoundError, AppointmentHoldService


def test_book_appointment_creates_appointment_and_marks_slot_booked() -> None:
    context = create_booking_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )

    result = context.booking_service.book_appointment(
        AppointmentBookingRequest(
            hold_id=hold.hold_id,
            availability_slot_id=context.slot.id,
            patient_id=context.patient.id,
            owner_id="call-123",
            reason="Annual skin check",
        ),
    )

    assert result.appointment.patient_id == context.patient.id
    assert result.appointment.doctor_id == context.doctor.id
    assert result.appointment.specialty_id == context.doctor.specialty_id
    assert result.appointment.availability_slot_id == context.slot.id
    assert result.appointment.status == AppointmentStatus.SCHEDULED
    assert result.appointment.reason == "Annual skin check"
    assert context.slot.status == AvailabilitySlotStatus.BOOKED


def test_book_appointment_does_not_release_hold_before_transaction_commit() -> None:
    context = create_booking_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )

    context.booking_service.book_appointment(
        AppointmentBookingRequest(
            hold_id=hold.hold_id,
            availability_slot_id=context.slot.id,
            patient_id=context.patient.id,
            owner_id="call-123",
        ),
    )

    stored_hold = context.hold_repository.get(
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
    )

    assert stored_hold is not None
    assert stored_hold.hold_id == hold.hold_id


def test_book_appointment_rejects_missing_owner() -> None:
    context = create_booking_context()

    with pytest.raises(AppointmentBookingOwnerRequiredError):
        context.booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=uuid4(),
                availability_slot_id=context.slot.id,
                patient_id=context.patient.id,
                owner_id=" ",
            ),
        )


def test_book_appointment_rejects_unknown_patient() -> None:
    context = create_booking_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )

    with pytest.raises(BookingPatientNotFoundError):
        context.booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=hold.hold_id,
                availability_slot_id=context.slot.id,
                patient_id=uuid4(),
                owner_id="call-123",
            ),
        )


def test_book_appointment_rejects_unavailable_slot() -> None:
    context = create_booking_context(slot_status=AvailabilitySlotStatus.BLOCKED)
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )

    with pytest.raises(BookingAvailabilitySlotUnavailableError):
        context.booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=hold.hold_id,
                availability_slot_id=context.slot.id,
                patient_id=context.patient.id,
                owner_id="call-123",
            ),
        )


def test_book_appointment_rejects_missing_hold() -> None:
    context = create_booking_context()

    with pytest.raises(AppointmentHoldNotFoundError):
        context.booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=uuid4(),
                availability_slot_id=context.slot.id,
                patient_id=context.patient.id,
                owner_id="call-123",
            ),
        )


def test_book_appointment_rejects_existing_scheduled_conflict() -> None:
    context = create_booking_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )
    existing_appointment = Appointment(
        id=uuid4(),
        patient_id=context.patient.id,
        doctor_id=context.doctor.id,
        specialty_id=context.doctor.specialty_id,
        availability_slot_id=context.slot.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        status=AppointmentStatus.SCHEDULED,
        reason="Existing appointment",
    )
    context.appointment_repository.appointments.append(existing_appointment)

    with pytest.raises(AppointmentSlotAlreadyBookedError):
        context.booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=hold.hold_id,
                availability_slot_id=context.slot.id,
                patient_id=context.patient.id,
                owner_id="call-123",
            ),
        )


class BookingContext:
    def __init__(
        self,
        *,
        patient: Patient,
        doctor: Doctor,
        slot: AvailabilitySlot,
        hold_repository: FakeAppointmentHoldRepository,
        appointment_repository: FakeAppointmentRepository,
        hold_service: AppointmentHoldService,
        booking_service: AppointmentBookingService,
    ) -> None:
        self.patient = patient
        self.doctor = doctor
        self.slot = slot
        self.hold_repository = hold_repository
        self.appointment_repository = appointment_repository
        self.hold_service = hold_service
        self.booking_service = booking_service


class FakePatientRepository:
    def __init__(self, patients: Sequence[Patient]) -> None:
        self.patients = list(patients)

    def get_by_id(self, patient_id: UUID) -> Patient | None:
        for patient in self.patients:
            if patient.id == patient_id:
                return patient

        return None

    def get_by_email(self, email: str) -> Patient | None:
        for patient in self.patients:
            if patient.email == email:
                return patient

        return None

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        for patient in self.patients:
            if patient.phone_number == phone_number:
                return patient

        return None

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        if phone_number is None and email is None:
            return None

        candidates: list[Patient] = []
        for patient in self.patients:
            if not is_exact_name_match(full_name, patient.full_name):
                continue

            if patient.date_of_birth != date_of_birth:
                continue

            if email is not None and patient.email.lower() != email.lower():
                continue

            candidates.append(patient)

        if not candidates:
            return None

        if phone_number is None:
            return candidates[0]

        normalized_phone = normalize_phone_digits(phone_number)
        for patient in candidates:
            if patient.phone_number is None:
                continue
            if normalize_phone_digits(patient.phone_number) == normalized_phone:
                return patient

        return None

    def list_by_date_of_birth(self, date_of_birth: date) -> list[Patient]:
        return [patient for patient in self.patients if patient.date_of_birth == date_of_birth]

    def add(self, patient: Patient) -> Patient:
        self.patients.append(patient)

        return patient


class FakeDoctorRepository:
    def __init__(self, doctors: Sequence[Doctor]) -> None:
        self.doctors = list(doctors)

    def list_active(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        doctors = [doctor for doctor in self.doctors if doctor.is_active]

        if specialty_id is not None:
            doctors = [doctor for doctor in doctors if doctor.specialty_id == specialty_id]

        return doctors

    def get_by_id(self, doctor_id: UUID) -> Doctor | None:
        for doctor in self.doctors:
            if doctor.id == doctor_id:
                return doctor

        return None


class FakeAvailabilitySlotRepository:
    def __init__(self, slots: Sequence[AvailabilitySlot]) -> None:
        self.slots = list(slots)

    def get_by_id(self, slot_id: UUID) -> AvailabilitySlot | None:
        for slot in self.slots:
            if slot.id == slot_id:
                return slot

        return None

    def list_available(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        return [
            slot
            for slot in self.slots
            if slot.doctor_id == doctor_id
            and slot.status == AvailabilitySlotStatus.AVAILABLE
            and slot.start_time >= start_from
            and slot.start_time < start_to
        ]


class FakeAppointmentRepository:
    def __init__(self, appointments: Sequence[Appointment]) -> None:
        self.appointments = list(appointments)

    def get_by_id(self, appointment_id: UUID) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.id == appointment_id:
                return appointment

        return None

    def list_upcoming_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        return [
            appointment
            for appointment in self.appointments
            if appointment.patient_id == patient_id
            and appointment.status == AppointmentStatus.SCHEDULED
            and appointment.start_time >= start_from
        ]

    def list_cancelable_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        return sorted(
            (
                appointment
                for appointment in self.appointments
                if appointment.patient_id == patient_id
                and appointment.status
                in (AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED)
                and appointment.start_time >= start_from
            ),
            key=lambda appointment: appointment.start_time,
        )

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.doctor_id != doctor_id:
                continue

            if appointment.start_time != start_time:
                continue

            if appointment.status != AppointmentStatus.SCHEDULED:
                continue

            return appointment

        return None

    def add(self, appointment: Appointment) -> Appointment:
        if appointment.id is None:
            appointment.id = uuid4()

        self.appointments.append(appointment)

        return appointment

    def find_by_rescheduled_from(
        self,
        *,
        appointment_id: UUID,
    ) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.rescheduled_from_appointment_id == appointment_id:
                return appointment

        return None


class FakeAppointmentHoldRepository:
    def __init__(self) -> None:
        self.holds: dict[tuple[UUID, datetime], AppointmentHold] = {}

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        key = (hold.doctor_id, hold.start_time)

        if key in self.holds:
            return False

        self.holds[key] = hold

        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        return self.holds.get((doctor_id, start_time))

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        for hold in self.holds.values():
            if hold.hold_id == hold_id:
                return hold

        return None

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        self.holds.pop((doctor_id, start_time), None)

    def find_held_availability_slot_ids(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[tuple[UUID, datetime]],
    ) -> set[UUID]:
        return {
            slot_id
            for slot_id, start_time in slots
            if (doctor_id, start_time) in self.holds
        }


def create_booking_context(
    *,
    slot_status: AvailabilitySlotStatus = AvailabilitySlotStatus.AVAILABLE,
) -> BookingContext:
    specialty = Specialty(
        id=uuid4(),
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=uuid4(),
        specialty_id=specialty.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=doctor.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=slot_status,
    )

    patient_repository = FakePatientRepository([patient])
    doctor_repository = FakeDoctorRepository([doctor])
    availability_repository = FakeAvailabilitySlotRepository([slot])
    appointment_repository = FakeAppointmentRepository([])
    hold_repository = FakeAppointmentHoldRepository()
    hold_service = AppointmentHoldService(
        repository=hold_repository,
        ttl_seconds=300,
    )
    booking_service = AppointmentBookingService(
        patients=patient_repository,
        doctors=doctor_repository,
        availability_slots=availability_repository,
        appointments=appointment_repository,
        hold_service=hold_service,
    )

    return BookingContext(
        patient=patient,
        doctor=doctor,
        slot=slot,
        hold_repository=hold_repository,
        appointment_repository=appointment_repository,
        hold_service=hold_service,
        booking_service=booking_service,
    )
