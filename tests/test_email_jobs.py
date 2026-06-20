from __future__ import annotations

from uuid import uuid4

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)


def test_enqueue_appointment_confirmation_creates_pending_email_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    patient_id = uuid4()

    email_job = service.enqueue_appointment_confirmation(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=appointment_id,
            patient_id=patient_id,
            recipient_email="patient@example.test",
            patient_name="John Miller",
            doctor_name="Dr. Emily Carter",
            appointment_start_time="2026-07-01T10:00:00+00:00",
            payload={"source": "retell_tool"},
        ),
    )

    assert email_job in repository.email_jobs
    assert email_job.job_type == EmailJobType.APPOINTMENT_CONFIRMATION
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.appointment_id == appointment_id
    assert email_job.patient_id == patient_id
    assert email_job.recipient_email == "patient@example.test"
    assert email_job.attempts == 0
    assert email_job.max_attempts == 3
    assert email_job.payload["source"] == "retell_tool"
    assert "John Miller" in email_job.body
    assert "Dr. Emily Carter" in email_job.body


def test_enqueue_appointment_confirmation_does_not_require_recipient_email_yet() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)

    email_job = service.enqueue_appointment_confirmation(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=uuid4(),
            patient_id=uuid4(),
        ),
    )

    assert email_job.recipient_email is None
    assert email_job.status == EmailJobStatus.PENDING


class FakeEmailJobRepository:
    def __init__(self) -> None:
        self.email_jobs: list[EmailJob] = []

    def add(self, email_job: EmailJob) -> EmailJob:
        self.email_jobs.append(email_job)

        return email_job