from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_job_metrics import EmailJobOperationalMetrics, EmailJobStatusCounts
from app.services.email_job_pagination import EmailJobCursor
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
    HumanEscalationNotificationEmailJobCreate,
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
    assert email_job.attempt_count == 0
    assert email_job.max_attempts == 3
    assert email_job.idempotency_key == f"appointment_confirmation:{appointment_id}"
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


def test_enqueue_human_escalation_notification_creates_pending_email_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    escalation_id = uuid4()
    conversation_id = uuid4()
    patient_id = uuid4()
    appointment_id = uuid4()

    email_job = service.enqueue_human_escalation_notification(
        HumanEscalationNotificationEmailJobCreate(
            escalation_id=escalation_id,
            conversation_id=conversation_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            summary="Patient asked to speak with a human.",
            reason="user_requested_human",
            priority="high",
            payload={"source": "chat"},
        ),
    )

    assert email_job in repository.email_jobs
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.appointment_id == appointment_id
    assert email_job.patient_id == patient_id
    assert email_job.recipient_email is None
    assert email_job.attempt_count == 0
    assert email_job.max_attempts == 3
    assert email_job.payload["human_escalation_id"] == str(escalation_id)
    assert email_job.payload["conversation_id"] == str(conversation_id)
    assert email_job.payload["source"] == "chat"
    assert "Patient asked to speak with a human." in email_job.body


def test_enqueue_human_escalation_notification_uses_conversation_id_for_missing_ids() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    conversation_id = uuid4()

    email_job = service.enqueue_human_escalation_notification(
        HumanEscalationNotificationEmailJobCreate(
            escalation_id=uuid4(),
            conversation_id=conversation_id,
        ),
    )

    assert email_job.appointment_id == conversation_id
    assert email_job.patient_id == conversation_id
    assert email_job.recipient_email is None


def test_retry_failed_email_job_moves_failed_job_to_pending() -> None:
    failed_job = create_email_job(
        status=EmailJobStatus.FAILED,
        attempt_count=2,
        last_error="smtp failure",
    )
    repository = FakeEmailJobRepository()
    repository.add(failed_job)
    service = EmailJobService(repository=repository)
    retry_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    result = service.retry_failed_email_job(failed_job.id, now=retry_at)

    assert result.status == EmailJobStatus.PENDING
    assert result.next_attempt_at == retry_at
    assert result.locked_by is None
    assert result.locked_until is None
    assert result.attempt_count == 2
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


def test_replay_failed_email_job_resets_failed_job_to_pending() -> None:
    original = create_email_job(
        status=EmailJobStatus.FAILED,
        attempt_count=3,
        last_error="max attempts exceeded",
        payload={"source": "test"},
    )
    repository = FakeEmailJobRepository()
    repository.add(original)
    service = EmailJobService(repository=repository)
    replay_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    replay = service.replay_failed_email_job(original.id, now=replay_at)

    assert original.id == replay.id
    assert replay.status == EmailJobStatus.PENDING
    assert replay.attempt_count == 0
    assert replay.next_attempt_at == replay_at
    assert replay.last_error is None
    assert len(repository.email_jobs) == 1


@pytest.mark.parametrize(
    "status",
    [
        EmailJobStatus.PENDING,
        EmailJobStatus.PROCESSING,
        EmailJobStatus.SENT,
    ],
)
def test_replay_failed_email_job_rejects_non_failed_status(
    status: EmailJobStatus,
) -> None:
    email_job = create_email_job(status=status)
    repository = FakeEmailJobRepository()
    repository.add(email_job)
    service = EmailJobService(repository=repository)

    with pytest.raises(InvalidEmailJobReplayStateError):
        service.replay_failed_email_job(email_job.id)


METRICS_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def test_get_operational_metrics_counts_jobs_by_status() -> None:
    repository = FakeEmailJobRepository()
    for status in (
        EmailJobStatus.PENDING,
        EmailJobStatus.PROCESSING,
        EmailJobStatus.SENT,
        EmailJobStatus.FAILED,
        EmailJobStatus.DEAD_LETTER,
    ):
        repository.add(create_email_job(status=status, locked_by=None))
    service = EmailJobService(repository=repository)

    metrics = service.get_operational_metrics(now=METRICS_NOW)

    assert metrics.total_jobs == 5
    assert metrics.counts_by_status.pending == 1
    assert metrics.counts_by_status.processing == 1
    assert metrics.counts_by_status.sent == 1
    assert metrics.counts_by_status.failed == 1
    assert metrics.counts_by_status.dead_letter == 1


def test_get_operational_metrics_detects_active_locks() -> None:
    repository = FakeEmailJobRepository()
    repository.add(
        create_email_job(
            status=EmailJobStatus.PROCESSING,
            locked_by="worker-1",
            locked_until=METRICS_NOW + timedelta(minutes=5),
        ),
    )
    service = EmailJobService(repository=repository)

    metrics = service.get_operational_metrics(now=METRICS_NOW)

    assert metrics.locked_count == 1


def test_get_operational_metrics_detects_expired_processing_locks() -> None:
    repository = FakeEmailJobRepository()
    repository.add(
        create_email_job(
            status=EmailJobStatus.PROCESSING,
            locked_by="worker-1",
            locked_until=METRICS_NOW - timedelta(minutes=1),
        ),
    )
    service = EmailJobService(repository=repository)

    metrics = service.get_operational_metrics(now=METRICS_NOW)

    assert metrics.expired_lock_count == 1


def test_get_operational_metrics_detects_overdue_pending_backlog() -> None:
    repository = FakeEmailJobRepository()
    repository.add(
        create_email_job(
            status=EmailJobStatus.PENDING,
            locked_by=None,
            next_attempt_at=METRICS_NOW - timedelta(hours=1),
        ),
    )
    repository.add(
        create_email_job(
            status=EmailJobStatus.PENDING,
            locked_by=None,
            next_attempt_at=None,
        ),
    )
    repository.add(
        create_email_job(
            status=EmailJobStatus.PENDING,
            locked_by=None,
            next_attempt_at=METRICS_NOW + timedelta(hours=1),
        ),
    )
    service = EmailJobService(repository=repository)

    metrics = service.get_operational_metrics(now=METRICS_NOW)

    assert metrics.overdue_pending_count == 2


def test_get_operational_metrics_exposes_oldest_and_newest_timestamps() -> None:
    repository = FakeEmailJobRepository()
    oldest_pending = create_email_job(
        status=EmailJobStatus.PENDING,
        locked_by=None,
        created_at=datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
    )
    repository.add(oldest_pending)
    repository.add(
        create_email_job(
            status=EmailJobStatus.PENDING,
            locked_by=None,
            created_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        ),
    )
    oldest_failed = create_email_job(
        status=EmailJobStatus.FAILED,
        locked_by=None,
        created_at=datetime(2026, 7, 1, 7, 0, tzinfo=UTC),
    )
    repository.add(oldest_failed)
    repository.add(
        create_email_job(
            status=EmailJobStatus.FAILED,
            locked_by=None,
            created_at=datetime(2026, 7, 1, 8, 30, tzinfo=UTC),
        ),
    )
    repository.add(
        create_email_job(
            status=EmailJobStatus.DEAD_LETTER,
            locked_by=None,
            created_at=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        ),
    )
    newest_dead_letter = create_email_job(
        status=EmailJobStatus.DEAD_LETTER,
        locked_by=None,
        created_at=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
    )
    repository.add(newest_dead_letter)
    service = EmailJobService(repository=repository)

    metrics = service.get_operational_metrics(now=METRICS_NOW)

    assert metrics.oldest_pending_created_at == oldest_pending.created_at
    assert metrics.oldest_failed_created_at == oldest_failed.created_at
    assert metrics.newest_dead_letter_created_at == newest_dead_letter.created_at


class FakeEmailJobRepository:
    def __init__(self) -> None:
        self.email_jobs: list[EmailJob] = []

    def add(self, email_job: EmailJob) -> EmailJob:
        if email_job.id is None:
            email_job.id = uuid4()

        if email_job.idempotency_key is not None:
            existing = self.get_by_idempotency_key(
                job_type=email_job.job_type,
                idempotency_key=email_job.idempotency_key,
            )
            if existing is not None:
                raise IntegrityError(
                    "duplicate idempotency key",
                    {},
                    Exception("duplicate idempotency key"),
                )

        self.email_jobs.append(email_job)

        return email_job

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return next(
            (email_job for email_job in self.email_jobs if email_job.id == email_job_id),
            None,
        )

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        return next(
            (
                email_job
                for email_job in self.email_jobs
                if email_job.job_type == job_type
                and email_job.idempotency_key == idempotency_key
            ),
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
        email_job.next_attempt_at = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

    def reset_for_replay(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.attempt_count = 0
        email_job.next_attempt_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

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
                if job.status == EmailJobStatus.PENDING
                and (
                    job.next_attempt_at is None
                    or job.next_attempt_at <= now
                )
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
    attempt_count: int = 1,
    last_error: str | None = "smtp failure",
    locked_by: str | None = "worker-1",
    locked_until: datetime | None = None,
    payload: dict[str, str] | None = None,
    created_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> EmailJob:
    effective_created_at = created_at or datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    effective_next_attempt_at = next_attempt_at
    effective_locked_until = locked_until
    if effective_locked_until is None and locked_by is not None:
        effective_locked_until = effective_created_at + timedelta(minutes=5)

    return EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=status,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="Your appointment is confirmed.",
        attempt_count=attempt_count,
        max_attempts=3,
        locked_by=locked_by,
        locked_until=effective_locked_until,
        last_error=last_error,
        payload=payload or {"source": "test"},
        next_attempt_at=effective_next_attempt_at,
        created_at=effective_created_at,
        updated_at=effective_created_at,
    )