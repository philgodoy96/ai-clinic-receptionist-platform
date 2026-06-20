from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.retell.appointment_booking_tools import RetellAppointmentBookingToolAdapter
from app.api.dependencies import (
    get_appointment_booking_service,
    get_appointment_hold_service,
    get_audit_log_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
    get_retell_appointment_booking_tool_adapter,
)
from app.db.session import get_db
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.main import create_app
from app.messaging.email_job_dispatch import NoopEmailJobDispatchPublisher
from app.models.audit import AuditLog
from app.models.email_jobs import EmailJob
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.appointment_booking import AppointmentBookingService
from app.services.appointment_holds import AppointmentHoldService
from app.services.audit_logs import AuditLogCreate, AuditLogService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)


@pytest.fixture()
def booking_context() -> BookingApiContext:
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
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_repository = FakeAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    appointment_repository = FakeAppointmentRepository()
    booking_service = AppointmentBookingService(
        patients=FakePatientRepository(patient),
        doctors=FakeDoctorRepository(doctor),
        availability_slots=FakeAvailabilitySlotRepository(slot),
        appointments=appointment_repository,
        hold_service=hold_service,
    )

    return BookingApiContext(
        patient=patient,
        doctor=doctor,
        slot=slot,
        hold_repository=hold_repository,
        hold_service=hold_service,
        appointment_repository=appointment_repository,
        booking_service=booking_service,
        db=FakeDatabaseSession(),
    )


@pytest.fixture()
def client(booking_context: BookingApiContext) -> Generator[TestClient, None, None]:
    app = create_app()
    audit_logs = FakeAuditLogService()
    email_jobs = FakeEmailJobService()

    def override_booking_service() -> AppointmentBookingService:
        return booking_context.booking_service

    def override_hold_service() -> AppointmentHoldService:
        return booking_context.hold_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield booking_context.db

    def override_audit_log_service() -> AuditLogService:
        return cast(AuditLogService, audit_logs)

    def override_email_job_service() -> EmailJobService:
        return cast(EmailJobService, email_jobs)

    def override_email_job_dispatch_publisher() -> NoopEmailJobDispatchPublisher:
        return NoopEmailJobDispatchPublisher()

    def override_retell_booking_adapter() -> RetellAppointmentBookingToolAdapter:
        return RetellAppointmentBookingToolAdapter(
            db=cast(Session, booking_context.db),
            booking_service=booking_context.booking_service,
            hold_service=booking_context.hold_service,
            audit_logs=cast(AuditLogService, audit_logs),
            email_jobs=cast(EmailJobService, email_jobs),
            email_job_dispatch=NoopEmailJobDispatchPublisher(),
        )

    app.dependency_overrides[get_appointment_booking_service] = override_booking_service
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_audit_log_service] = override_audit_log_service
    app.dependency_overrides[get_email_job_service] = override_email_job_service
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )
    app.dependency_overrides[get_retell_appointment_booking_tool_adapter] = (
        override_retell_booking_adapter
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_booking_api_creates_appointment_and_releases_hold(
    client: TestClient,
    booking_context: BookingApiContext,
) -> None:
    hold = booking_context.create_hold(owner_id="chat-123")

    response = client.post(
        "/api/v1/scheduling/appointments/book",
        json={
            "hold_id": str(hold.hold_id),
            "availability_slot_id": str(booking_context.slot.id),
            "patient_id": str(booking_context.patient.id),
            "owner_id": "chat-123",
            "reason": "Skin check",
        },
    )

    stored_hold = booking_context.hold_repository.get(
        doctor_id=booking_context.doctor.id,
        start_time=booking_context.slot.start_time,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "scheduled"
    assert booking_context.slot.status == AvailabilitySlotStatus.BOOKED
    assert stored_hold is None


def test_booking_api_rejects_expired_hold(
    client: TestClient,
    booking_context: BookingApiContext,
) -> None:
    response = client.post(
        "/api/v1/scheduling/appointments/book",
        json={
            "hold_id": str(uuid4()),
            "availability_slot_id": str(booking_context.slot.id),
            "patient_id": str(booking_context.patient.id),
            "owner_id": "chat-123",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "appointment hold was not found or expired"


def test_retell_booking_tool_books_appointment_with_call_id(
    client: TestClient,
    booking_context: BookingApiContext,
) -> None:
    hold = booking_context.create_hold(owner_id="retell-call-123")

    response = client.post(
        "/api/v1/retell/tools/book-appointment",
        json={
            "hold_id": str(hold.hold_id),
            "availability_slot_id": str(booking_context.slot.id),
            "patient_id": str(booking_context.patient.id),
            "call_id": "retell-call-123",
            "reason": "Skin check",
        },
    )

    stored_hold = booking_context.hold_repository.get(
        doctor_id=booking_context.doctor.id,
        start_time=booking_context.slot.start_time,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["result"]["appointment"]["status"] == "scheduled"
    assert stored_hold is None


def test_retell_booking_tool_returns_structured_error_without_owner(
    client: TestClient,
    booking_context: BookingApiContext,
) -> None:
    hold = booking_context.create_hold(owner_id="retell-call-123")

    response = client.post(
        "/api/v1/retell/tools/book-appointment",
        json={
            "hold_id": str(hold.hold_id),
            "availability_slot_id": str(booking_context.slot.id),
            "patient_id": str(booking_context.patient.id),
            "reason": "Skin check",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "missing_booking_owner"


class BookingApiContext:
    def __init__(
        self,
        *,
        patient: Patient,
        doctor: Doctor,
        slot: AvailabilitySlot,
        hold_repository: FakeAppointmentHoldRepository,
        hold_service: AppointmentHoldService,
        appointment_repository: FakeAppointmentRepository,
        booking_service: AppointmentBookingService,
        db: FakeDatabaseSession,
    ) -> None:
        self.patient = patient
        self.doctor = doctor
        self.slot = slot
        self.hold_repository = hold_repository
        self.hold_service = hold_service
        self.appointment_repository = appointment_repository
        self.booking_service = booking_service
        self.db = db

    def create_hold(self, *, owner_id: str) -> AppointmentHold:
        return self.hold_service.create_hold(
            availability_slot_id=self.slot.id,
            doctor_id=self.doctor.id,
            start_time=self.slot.start_time,
            end_time=self.slot.end_time,
            owner_id=owner_id,
        )


class FakeDatabaseSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.refreshed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, instance: object) -> None:
        self.refreshed = True


class FakeAuditLogService:
    def __init__(self) -> None:
        self.records: list[AuditLogCreate] = []

    def record(self, payload: AuditLogCreate) -> AuditLog:
        self.records.append(payload)
        return AuditLog(
            event_type=payload.event_type,
            outcome=payload.outcome,
            actor_type=payload.actor_type,
            source=payload.source,
            event_metadata=payload.event_metadata,
        )

    def record_best_effort(self, payload: AuditLogCreate) -> None:
        self.record(payload)


class FakeEmailJobService:
    def __init__(self) -> None:
        self.jobs: list[AppointmentConfirmationEmailJobCreate] = []

    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        self.jobs.append(payload)
        return EmailJob(
            appointment_id=payload.appointment_id,
            patient_id=payload.patient_id,
            subject="Appointment confirmation",
            body="test",
        )


class FakePatientRepository:
    def __init__(self, patient: Patient) -> None:
        self.patient = patient

    def get_by_id(self, patient_id: UUID) -> Patient | None:
        if patient_id == self.patient.id:
            return self.patient

        return None

    def get_by_email(self, email: str) -> Patient | None:
        if email == self.patient.email:
            return self.patient

        return None

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        if phone_number == self.patient.phone_number:
            return self.patient

        return None

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        return self.patient

    def add(self, patient: Patient) -> Patient:
        return patient


class FakeDoctorRepository:
    def __init__(self, doctor: Doctor) -> None:
        self.doctor = doctor

    def list_active(self, specialty_id: UUID | None = None) -> list[Doctor]:
        return [self.doctor]

    def get_by_id(self, doctor_id: UUID) -> Doctor | None:
        if doctor_id == self.doctor.id:
            return self.doctor

        return None


class FakeAvailabilitySlotRepository:
    def __init__(self, slot: AvailabilitySlot) -> None:
        self.slot = slot

    def get_by_id(self, slot_id: UUID) -> AvailabilitySlot | None:
        if slot_id == self.slot.id:
            return self.slot

        return None

    def list_available(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> list[AvailabilitySlot]:
        if (
            doctor_id == self.slot.doctor_id
            and self.slot.status == AvailabilitySlotStatus.AVAILABLE
        ):
            return [self.slot]

        return []


class FakeAppointmentRepository:
    def __init__(self) -> None:
        self.appointments: list[Appointment] = []

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
    ) -> list[Appointment]:
        return [
            appointment
            for appointment in self.appointments
            if appointment.patient_id == patient_id
        ]

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        for appointment in self.appointments:
            if (
                appointment.doctor_id == doctor_id
                and appointment.start_time == start_time
                and appointment.status == AppointmentStatus.SCHEDULED
            ):
                return appointment

        return None

    def add(self, appointment: Appointment) -> Appointment:
        if appointment.id is None:
            appointment.id = uuid4()

        self.appointments.append(appointment)

        return appointment


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

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        self.holds.pop((doctor_id, start_time), None)