from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_job_metrics import EmailJobOperationalMetrics, EmailJobStatusCounts
from app.services.email_job_pagination import EmailJobCursor
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
    InvalidEmailJobReplayStateError,
    InvalidEmailJobRetryStateError,
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


def test_retry_failed_email_job_moves_failed_job_to_pending() -> None:
    failed_job = create_email_job(
        status=EmailJobStatus.FAILED,
        attempts=2,
        last_error="smtp failure",
    )
    repository = FakeEmailJobRepository()
    repository.add(failed_job)
    service = EmailJobService(repository=repository)
    retry_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    result = service.retry_failed_email_job(failed_job.id, now=retry_at)

    assert result.status == EmailJobStatus.PENDING
    assert result.scheduled_for == retry_at
    assert result.locked_by is None
    assert result.locked_until is None
    assert result.attempts == 2
    assert result.last_error == "smtp failure"


@pytest.mark.parametrize(
    "status",
    [
        EmailJobStatus.PENDING,
        EmailJobStatus.SENT,
        EmailJobStatus.PROCESSING,
        EmailJobStatus.DEAD_LETTER,
    ],
)
def test_retry_failed_email_job_rejects_non_failed_status(
    status: EmailJobStatus,
) -> None:
    email_job = create_email_job(status=status)
    repository = FakeEmailJobRepository()
    repository.add(email_job)
    service = EmailJobService(repository=repository)

    with pytest.raises(InvalidEmailJobRetryStateError):
        service.retry_failed_email_job(email_job.id)


def test_replay_dead_letter_email_job_creates_new_pending_job() -> None:
    original = create_email_job(
        status=EmailJobStatus.DEAD_LETTER,
        attempts=3,
        last_error="max attempts exceeded",
        payload={"source": "test"},
    )
    repository = FakeEmailJobRepository()
    repository.add(original)
    service = EmailJobService(repository=repository)
    replay_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    replay = service.replay_dead_letter_email_job(original.id, now=replay_at)

    assert original.status == EmailJobStatus.DEAD_LETTER
    assert replay.id != original.id
    assert replay.status == EmailJobStatus.PENDING
    assert replay.attempts == 0
    assert replay.payload["replayed_from_email_job_id"] == str(original.id)
    assert replay.last_error is None
    assert len(repository.email_jobs) == 2


@pytest.mark.parametrize(
    "status",
    [
        EmailJobStatus.FAILED,
        EmailJobStatus.PENDING,
        EmailJobStatus.PROCESSING,
        EmailJobStatus.SENT,
    ],
)
def test_replay_dead_letter_email_job_rejects_non_dead_letter_status(
    status: EmailJobStatus,
) -> None:
    email_job = create_email_job(status=status)
    repository = FakeEmailJobRepository()
    repository.add(email_job)
    service = EmailJobService(repository=repository)

    with pytest.raises(InvalidEmailJobReplayStateError):
        service.replay_dead_letter_email_job(email_job.id)


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

    def schedule_retry(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.scheduled_for = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

    def create_replay(
        self,
        *,
        original_email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        replay_job = EmailJob(
            id=uuid4(),
            job_type=original_email_job.job_type,
            status=EmailJobStatus.PENDING,
            appointment_id=original_email_job.appointment_id,
            patient_id=original_email_job.patient_id,
            recipient_email=original_email_job.recipient_email,
            subject=original_email_job.subject,
            body=original_email_job.body,
            attempts=0,
            max_attempts=original_email_job.max_attempts,
            locked_by=None,
            locked_until=None,
            last_error=None,
            payload={
                **original_email_job.payload,
                "replayed_from_email_job_id": str(original_email_job.id),
                "replayed_from_attempts": original_email_job.attempts,
                "replayed_from_status": original_email_job.status.value,
            },
            scheduled_for=now,
            sent_at=None,
            created_at=now,
            updated_at=now,
        )

        return self.add(replay_job)

    def get_operational_metrics(
        self,
        *,
        now: datetime,
    ) -> EmailJobOperationalMetrics:
        jobs = self.email_jobs
        pending_jobs = [job for job in jobs if job.status == EmailJobStatus.PENDING]
        failed_jobs = [job for job in jobs if job.status == EmailJobStatus.FAILED]
        dead_letter_jobs = [job for job in jobs if job.status == EmailJobStatus.DEAD_LETTER]

        return EmailJobOperationalMetrics(
            total_jobs=len(jobs),
            counts_by_status=EmailJobStatusCounts(
                pending=len(pending_jobs),
                processing=sum(
                    1 for job in jobs if job.status == EmailJobStatus.PROCESSING
                ),
                sent=sum(1 for job in jobs if job.status == EmailJobStatus.SENT),
                failed=len(failed_jobs),
                dead_letter=len(dead_letter_jobs),
            ),
            locked_count=sum(
                1
                for job in jobs
                if job.locked_until is not None and job.locked_until >= now
            ),
            expired_lock_count=sum(
                1
                for job in jobs
                if job.status == EmailJobStatus.PROCESSING
                and job.locked_until is not None
                and job.locked_until < now
            ),
            overdue_pending_count=sum(
                1
                for job in jobs
                if job.status in (EmailJobStatus.PENDING, EmailJobStatus.FAILED)
                and job.scheduled_for <= now
            ),
            oldest_pending_created_at=(
                min((job.created_at for job in pending_jobs), default=None)
            ),
            oldest_failed_created_at=(
                min((job.created_at for job in failed_jobs), default=None)
            ),
            newest_dead_letter_created_at=(
                max((job.created_at for job in dead_letter_jobs), default=None)
            ),
        )


def create_email_job(
    *,
    status: EmailJobStatus,
    attempts: int = 1,
    last_error: str | None = "smtp failure",
    locked_by: str | None = "worker-1",
    locked_until: datetime | None = None,
    payload: dict[str, str] | None = None,
) -> EmailJob:
    created_at = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    effective_locked_until = locked_until
    if effective_locked_until is None and locked_by is not None:
        effective_locked_until = created_at + timedelta(minutes=5)

    return EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=status,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="Your appointment is confirmed.",
        attempts=attempts,
        max_attempts=3,
        locked_by=locked_by,
        locked_until=effective_locked_until,
        last_error=last_error,
        payload=payload or {"source": "test"},
        scheduled_for=created_at,
        created_at=created_at,
        updated_at=created_at,
    )