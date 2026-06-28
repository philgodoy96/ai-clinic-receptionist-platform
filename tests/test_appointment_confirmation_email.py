from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.appointment_confirmation_email import (
    build_appointment_confirmation_email_job_create,
)
from app.services.email_jobs import EmailJobService
from tests.test_email_jobs import FakeEmailJobRepository


def test_builder_populates_enriched_confirmation_fields() -> None:
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
    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=slot.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
    )

    payload = build_appointment_confirmation_email_job_create(
        appointment=appointment,
        patient=patient,
        doctor=doctor,
        specialty_name=specialty.name,
        source="scheduling_api",
        extra_payload={"hold_id": "hold-123"},
    )

    assert payload.recipient_email == patient.email
    assert payload.patient_name == patient.full_name
    assert payload.doctor_name == doctor.full_name
    assert payload.appointment_start_time == start_time.isoformat()
    assert payload.payload["source"] == "scheduling_api"
    assert payload.payload["specialty_name"] == specialty.name
    assert payload.payload["hold_id"] == "hold-123"


def test_builder_remains_compatible_with_existing_email_job_service() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    patient_id = uuid4()

    payload = build_appointment_confirmation_email_job_create(
        appointment=Appointment(
            id=appointment_id,
            patient_id=patient_id,
            doctor_id=uuid4(),
            specialty_id=uuid4(),
            availability_slot_id=uuid4(),
            start_time=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 1, 10, 30, tzinfo=UTC),
            status=AppointmentStatus.SCHEDULED,
        ),
        source="chat_booking",
    )

    first = service.get_or_create_appointment_confirmation_email_job(payload)
    second = service.get_or_create_appointment_confirmation_email_job(payload)

    assert first.created is True
    assert second.created is False
    assert len(repository.email_jobs) == 1
    assert repository.email_jobs[0].idempotency_key == (
        f"appointment_confirmation:{appointment_id}"
    )
