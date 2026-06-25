from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_scheduling_service
from app.domain.scheduling.availability import AvailabilityCheckStatus
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.main import create_app
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.scheduling import (
    AvailabilityCheckResult,
    DoctorNotFoundError,
    InsufficientPatientIdentityError,
    InvalidAvailabilityWindowError,
    PatientLookupCriteria,
)


@pytest.fixture()
def fake_service() -> FakeSchedulingService:
    specialty_id = uuid4()
    doctor_id = uuid4()
    patient_id = uuid4()
    slot_id = uuid4()
    appointment_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    specialty = Specialty(
        id=specialty_id,
        name="Primary Care",
        description="General health visits",
        is_active=True,
    )
    doctor = Doctor(
        id=doctor_id,
        specialty_id=specialty_id,
        full_name="Dr. Sarah Mitchell",
        email="sarah.mitchell@example-clinic.test",
        phone_number="+1-555-0103",
        is_active=True,
    )
    patient = Patient(
        id=patient_id,
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    slot = AvailabilitySlot(
        id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    appointment = Appointment(
        id=appointment_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        availability_slot_id=slot_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )

    return FakeSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        patients=[patient],
        availability_slots=[slot],
        appointments=[appointment],
    )


@pytest.fixture()
def client(fake_service: FakeSchedulingService) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_scheduling_service() -> FakeSchedulingService:
        return fake_service

    app.dependency_overrides[get_scheduling_service] = override_scheduling_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_list_specialties_returns_specialties(client: TestClient) -> None:
    response = client.get("/api/v1/scheduling/specialties")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Primary Care"


def test_list_doctors_returns_doctors(client: TestClient) -> None:
    response = client.get("/api/v1/scheduling/doctors")

    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "Dr. Sarah Mitchell"


def test_check_availability_returns_available_slots(client: TestClient) -> None:
    doctor_id = client.get("/api/v1/scheduling/doctors").json()[0]["id"]

    response = client.get(
        f"/api/v1/scheduling/doctors/{doctor_id}/availability",
        params={
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "available"


def test_check_availability_rejects_invalid_window(client: TestClient) -> None:
    doctor_id = client.get("/api/v1/scheduling/doctors").json()[0]["id"]

    response = client.get(
        f"/api/v1/scheduling/doctors/{doctor_id}/availability",
        params={
            "start_from": "2026-07-01T10:00:00Z",
            "start_to": "2026-07-01T10:00:00Z",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["message"] == "start_to must be greater than start_from"
    assert body["error"]["code"] == "invalid_availability_window"


def test_check_availability_returns_not_found_for_unknown_doctor(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/scheduling/doctors/{uuid4()}/availability",
        params={
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["message"] == "doctor was not found or is inactive"
    assert body["error"]["code"] == "doctor_not_found"


def test_lookup_patient_returns_patient_when_identity_is_sufficient(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/patients/lookup",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
            "phone_number": "+1-555-0201",
        },
    )

    assert response.status_code == 200
    assert response.json()["email"] == "john.miller@example.test"


def test_lookup_patient_rejects_insufficient_identity(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/patients/lookup",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["message"] == "patient lookup requires phone_number or email"
    assert body["error"]["code"] == "invalid_patient_identity"


def test_lookup_patient_returns_not_found_for_unknown_patient(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/patients/lookup",
        json={
            "full_name": "Unknown Patient",
            "date_of_birth": "1985-04-12",
            "phone_number": "+1-555-9999",
        },
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["message"] == "patient was not found"
    assert body["error"]["code"] == "patient_not_found"


def test_list_upcoming_appointments_returns_appointments(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/patients/upcoming-appointments",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
            "email": "john.miller@example.test",
            "start_from": "2026-07-01T09:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "scheduled"


def test_list_upcoming_appointments_rejects_insufficient_identity(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/patients/upcoming-appointments",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
            "start_from": "2026-07-01T09:00:00Z",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["message"] == "patient lookup requires phone_number or email"
    assert body["error"]["code"] == "invalid_patient_identity"


class FakeSchedulingService:
    def __init__(
        self,
        *,
        specialties: Sequence[Specialty],
        doctors: Sequence[Doctor],
        patients: Sequence[Patient],
        availability_slots: Sequence[AvailabilitySlot],
        appointments: Sequence[Appointment],
    ) -> None:
        self.specialties = list(specialties)
        self.doctors = list(doctors)
        self.patients = list(patients)
        self.availability_slots = list(availability_slots)
        self.appointments = list(appointments)

    def list_specialties(self) -> Sequence[Specialty]:
        return self.specialties

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        if specialty_id is None:
            return self.doctors

        return [doctor for doctor in self.doctors if doctor.specialty_id == specialty_id]

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        if start_to <= start_from:
            raise InvalidAvailabilityWindowError

        if not any(doctor.id == doctor_id and doctor.is_active for doctor in self.doctors):
            raise DoctorNotFoundError

        return [
            slot
            for slot in self.availability_slots
            if slot.doctor_id == doctor_id
            and slot.status == AvailabilitySlotStatus.AVAILABLE
            and slot.start_time >= start_from
            and slot.start_time < start_to
        ]

    def check_availability_with_status(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> AvailabilityCheckResult:
        slots = self.check_availability(
            doctor_id=doctor_id,
            start_from=start_from,
            start_to=start_to,
        )
        status = (
            AvailabilityCheckStatus.AVAILABLE
            if slots
            else AvailabilityCheckStatus.NO_MATCHING_SLOTS
        )
        return AvailabilityCheckResult(status=status, available_slots=list(slots))

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        if not criteria.has_sufficient_identifiers():
            raise InsufficientPatientIdentityError

        for patient in self.patients:
            if patient.full_name != criteria.full_name:
                continue

            if patient.date_of_birth != criteria.date_of_birth:
                continue

            if criteria.phone_number is not None and patient.phone_number != criteria.phone_number:
                continue

            if criteria.email is not None and patient.email != criteria.email:
                continue

            return patient

        return None

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        patient = self.lookup_patient(criteria)

        if patient is None:
            return []

        return [
            appointment
            for appointment in self.appointments
            if appointment.patient_id == patient.id
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
            if appointment.doctor_id == doctor_id and appointment.start_time == start_time:
                return appointment

        return None
