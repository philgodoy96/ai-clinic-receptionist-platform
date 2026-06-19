from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.scheduling import (
    DoctorNotFoundError,
    InsufficientPatientIdentityError,
    InvalidAvailabilityWindowError,
    PatientLookupCriteria,
    SchedulingService,
)


def test_list_specialties_returns_active_specialties() -> None:
    service = create_service(
        specialties=[create_specialty(name="Dermatology")],
    )

    specialties = service.list_specialties()

    assert [specialty.name for specialty in specialties] == ["Dermatology"]


def test_list_doctors_returns_active_doctors() -> None:
    specialty = create_specialty(name="Primary Care")
    doctor = create_doctor(specialty_id=specialty.id)
    service = create_service(
        specialties=[specialty],
        doctors=[doctor],
    )

    doctors = service.list_doctors(specialty_id=specialty.id)

    assert [item.full_name for item in doctors] == ["Dr. Sarah Mitchell"]


def test_check_availability_rejects_invalid_time_window() -> None:
    doctor = create_doctor()
    service = create_service(doctors=[doctor])
    start_from = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(InvalidAvailabilityWindowError):
        service.check_availability(
            doctor_id=doctor.id,
            start_from=start_from,
            start_to=start_from,
        )


def test_check_availability_rejects_missing_doctor() -> None:
    service = create_service()
    start_from = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(DoctorNotFoundError):
        service.check_availability(
            doctor_id=uuid4(),
            start_from=start_from,
            start_to=start_from + timedelta(hours=1),
        )


def test_check_availability_returns_available_slots() -> None:
    doctor = create_doctor()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    available_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=start_time,
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    blocked_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=start_time + timedelta(hours=1),
        status=AvailabilitySlotStatus.BLOCKED,
    )
    service = create_service(
        doctors=[doctor],
        availability_slots=[available_slot, blocked_slot],
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=start_time - timedelta(minutes=1),
        start_to=start_time + timedelta(hours=2),
    )

    assert [slot.id for slot in slots] == [available_slot.id]


def test_lookup_patient_rejects_insufficient_identity() -> None:
    service = create_service()

    with pytest.raises(InsufficientPatientIdentityError):
        service.lookup_patient(
            PatientLookupCriteria(
                full_name="John Miller",
                date_of_birth=date(1985, 4, 12),
            ),
        )


def test_lookup_patient_returns_patient_when_identity_is_sufficient() -> None:
    patient = create_patient()
    service = create_service(patients=[patient])

    found = service.lookup_patient(
        PatientLookupCriteria(
            full_name="John Miller",
            date_of_birth=date(1985, 4, 12),
            phone_number="+1-555-0201",
        ),
    )

    assert found is not None
    assert found.id == patient.id


def test_list_upcoming_appointments_returns_empty_when_patient_is_unknown() -> None:
    service = create_service()

    appointments = service.list_upcoming_appointments(
        criteria=PatientLookupCriteria(
            full_name="Unknown Patient",
            date_of_birth=date(1985, 4, 12),
            phone_number="+1-555-9999",
        ),
        start_from=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )

    assert appointments == []


def test_list_upcoming_appointments_requires_sufficient_patient_identity() -> None:
    service = create_service()

    with pytest.raises(InsufficientPatientIdentityError):
        service.list_upcoming_appointments(
            criteria=PatientLookupCriteria(
                full_name="John Miller",
                date_of_birth=date(1985, 4, 12),
            ),
            start_from=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )


def test_list_upcoming_appointments_returns_scheduled_appointments() -> None:
    patient = create_patient()
    doctor = create_doctor()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment = create_appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time,
        status=AppointmentStatus.SCHEDULED,
    )
    service = create_service(
        patients=[patient],
        appointments=[appointment],
    )

    appointments = service.list_upcoming_appointments(
        criteria=PatientLookupCriteria(
            full_name="John Miller",
            date_of_birth=date(1985, 4, 12),
            email="john.miller@example.test",
        ),
        start_from=start_time - timedelta(minutes=1),
    )

    assert [item.id for item in appointments] == [appointment.id]


def test_find_scheduled_conflict_returns_existing_conflict() -> None:
    patient = create_patient()
    doctor = create_doctor()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment = create_appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time,
        status=AppointmentStatus.SCHEDULED,
    )
    service = create_service(appointments=[appointment])

    conflict = service.find_scheduled_conflict(
        doctor_id=doctor.id,
        start_time=start_time,
    )

    assert conflict is not None
    assert conflict.id == appointment.id


class FakeSpecialtyRepository:
    def __init__(self, specialties: Sequence[Specialty]) -> None:
        self.specialties = list(specialties)

    def list_active(self) -> Sequence[Specialty]:
        return [specialty for specialty in self.specialties if specialty.is_active]

    def get_by_name(self, name: str) -> Specialty | None:
        for specialty in self.specialties:
            if specialty.name == name:
                return specialty

        return None

    def get_by_id(self, specialty_id: UUID) -> Specialty | None:
        for specialty in self.specialties:
            if specialty.id == specialty_id:
                return specialty

        return None


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
        for patient in self.patients:
            if patient.full_name != full_name:
                continue

            if patient.date_of_birth != date_of_birth:
                continue

            if phone_number is not None and patient.phone_number != phone_number:
                continue

            if email is not None and patient.email != email:
                continue

            if phone_number is None and email is None:
                return None

            return patient

        return None

    def add(self, patient: Patient) -> Patient:
        self.patients.append(patient)

        return patient


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
        self.appointments.append(appointment)

        return appointment


def create_service(
    *,
    specialties: Sequence[Specialty] = (),
    doctors: Sequence[Doctor] = (),
    patients: Sequence[Patient] = (),
    availability_slots: Sequence[AvailabilitySlot] = (),
    appointments: Sequence[Appointment] = (),
) -> SchedulingService:
    return SchedulingService(
        specialties=FakeSpecialtyRepository(specialties),
        doctors=FakeDoctorRepository(doctors),
        patients=FakePatientRepository(patients),
        availability_slots=FakeAvailabilitySlotRepository(availability_slots),
        appointments=FakeAppointmentRepository(appointments),
    )


def create_specialty(
    *,
    specialty_id: UUID | None = None,
    name: str = "Primary Care",
    is_active: bool = True,
) -> Specialty:
    return Specialty(
        id=specialty_id or uuid4(),
        name=name,
        description="General care",
        is_active=is_active,
    )


def create_doctor(
    *,
    doctor_id: UUID | None = None,
    specialty_id: UUID | None = None,
    is_active: bool = True,
) -> Doctor:
    return Doctor(
        id=doctor_id or uuid4(),
        specialty_id=specialty_id or uuid4(),
        full_name="Dr. Sarah Mitchell",
        email="sarah.mitchell@example-clinic.test",
        phone_number="+1-555-0103",
        is_active=is_active,
    )


def create_patient(*, patient_id: UUID | None = None) -> Patient:
    return Patient(
        id=patient_id or uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )


def create_availability_slot(
    *,
    slot_id: UUID | None = None,
    doctor_id: UUID,
    start_time: datetime,
    status: AvailabilitySlotStatus,
) -> AvailabilitySlot:
    return AvailabilitySlot(
        id=slot_id or uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
    )


def create_appointment(
    *,
    appointment_id: UUID | None = None,
    patient_id: UUID,
    doctor_id: UUID,
    specialty_id: UUID,
    start_time: datetime,
    status: AppointmentStatus,
) -> Appointment:
    return Appointment(
        id=appointment_id or uuid4(),
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
        reason="Annual checkup",
    )