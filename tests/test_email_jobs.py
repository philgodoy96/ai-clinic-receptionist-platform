from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_job_pagination import EmailJobCursor
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

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return next(
            (email_job for email_job in self.email_jobs if email_job.id == email_job_id),
            None,
        )

    def list_recent(
        self,
        *,
        limit: int,
        cursor: EmailJobCursor | None = None,
        job_type: EmailJobType | None = None,
        status: EmailJobStatus | None = None,
        appointment_id: UUID | None = None,
        patient_id: UUID | None = None,
    ) -> Sequence[EmailJob]:
        jobs = sorted(
            self.email_jobs,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

        if cursor is not None:
            jobs = [
                item
                for item in jobs
                if (item.created_at, item.id) < (cursor.created_at, cursor.id)
            ]

        if job_type is not None:
            jobs = [item for item in jobs if item.job_type == job_type]

        if status is not None:
            jobs = [item for item in jobs if item.status == status]

        if appointment_id is not None:
            jobs = [item for item in jobs if item.appointment_id == appointment_id]

        if patient_id is not None:
            jobs = [item for item in jobs if item.patient_id == patient_id]

        return jobs[:limit]