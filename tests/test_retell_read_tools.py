from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import get_retell_scheduling_tool_adapter
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.main import create_app
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.scheduling import (
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
        name="Dermatology",
        description="Skin, hair, and nail care.",
        is_active=True,
    )
    doctor = Doctor(
        id=doctor_id,
        specialty_id=specialty_id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
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
        reason="Skin check",
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

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(fake_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_retell_list_specialties_returns_tool_response(client: TestClient) -> None:
    response = client.post("/api/v1/retell/tools/list-specialties", json={})

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["specialties"][0]["name"] == "Dermatology"


def test_retell_list_doctors_filters_by_specialty_name(client: TestClient) -> None:
    response = client.post(
        "/api/v1/retell/tools/list-doctors",
        json={"specialty_name": "Dermatology"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["doctors"][0]["full_name"] == "Dr. Emily Carter"


def test_retell_list_doctors_filters_by_human_doctor_name(client: TestClient) -> None:
    response = client.post(
        "/api/v1/retell/tools/list-doctors",
        json={"doctor_name": "Dr Carter"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["doctors"][0]["full_name"] == "Dr. Emily Carter"


def test_retell_check_availability_resolves_doctor_by_name(client: TestClient) -> None:
    response = client.post(
        "/api/v1/retell/tools/check-availability",
        json={
            "doctor_name": "Emily Carter",
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["available_slots"][0]["status"] == "available"


def test_retell_check_availability_returns_error_for_unknown_specialty(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/check-availability",
        json={
            "specialty_name": "Neurology",
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is False
    assert body["error_code"] == "specialty_not_found"


def test_retell_check_availability_returns_error_for_invalid_window(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/check-availability",
        json={
            "doctor_name": "Emily Carter",
            "start_from": "2026-07-01T10:00:00Z",
            "start_to": "2026-07-01T10:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is False
    assert body["error_code"] == "invalid_availability_window"


def test_retell_lookup_patient_returns_patient_when_identity_is_sufficient(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/lookup-patient",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
            "phone_number": "+1-555-0201",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["found"] is True
    assert body["result"]["patient"]["email"] == "john.miller@example.test"


def test_retell_lookup_patient_rejects_insufficient_identity(client: TestClient) -> None:
    response = client.post(
        "/api/v1/retell/tools/lookup-patient",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is False
    assert body["error_code"] == "insufficient_patient_identity"


def test_retell_upcoming_appointments_returns_patient_appointments(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/list-upcoming-appointments",
        json={
            "full_name": "John Miller",
            "date_of_birth": "1985-04-12",
            "email": "john.miller@example.test",
            "start_from": "2026-07-01T09:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["patient_found"] is True
    assert body["result"]["appointments"][0]["status"] == "scheduled"


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