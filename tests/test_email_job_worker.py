from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.jobs.email_job_retry import calculate_email_job_backoff_seconds
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_worker import EmailJobWorkerService


def test_email_job_worker_marks_job_sent() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is True
    assert result.job_id == email_job.id
    assert result.status == EmailJobStatus.SENT
    assert email_job.status == EmailJobStatus.SENT
    assert email_job.sent_at == now
    assert email_job.locked_by is None
    assert email_job.locked_until is None
    assert len(provider.sent_messages) == 1
    assert provider.sent_messages[0].to == "patient@example.test"


def test_email_job_worker_marks_job_failed_when_provider_fails() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider(should_fail=True)
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
        backoff_base_seconds=30,
        backoff_max_seconds=900,
    )

    result = worker.process_one(now=now)

    assert result.processed is True
    assert result.status == EmailJobStatus.PENDING
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert email_job.last_error == "fake email provider failure"
    assert email_job.next_attempt_at == now + timedelta(seconds=30)
    assert email_job.locked_by is None
    assert email_job.locked_until is None


def test_email_job_worker_marks_job_failed_after_max_attempts() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(attempt_count=2, max_attempts=3)
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider(should_fail=True)
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.FAILED
    assert email_job.status == EmailJobStatus.FAILED
    assert email_job.attempt_count == 3
    assert email_job.last_error == "fake email provider failure"


def test_email_job_worker_returns_not_processed_when_no_job_is_available() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    repository = FakeEmailJobWorkerRepository([])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert result.job_id is None
    assert result.status is None


def test_email_job_worker_skips_locked_job() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(
        locked_by="worker-2",
        locked_until=now + timedelta(minutes=5),
    )
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert email_job.status == EmailJobStatus.PENDING


def test_email_job_worker_recovers_expired_lock() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(
        status=EmailJobStatus.PROCESSING,
        locked_by="worker-2",
        locked_until=now - timedelta(minutes=1),
    )
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.SENT
    assert email_job.sent_at == now


class FakeEmailJobWorkerRepository:
    def __init__(self, email_jobs: list[EmailJob]) -> None:
        self.email_jobs = email_jobs

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return next(
            (email_job for email_job in self.email_jobs if email_job.id == email_job_id),
            None,
        )

    def claim_next_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        available_jobs = [
            email_job
            for email_job in self.email_jobs
            if (
                email_job.status == EmailJobStatus.PENDING
                and (email_job.next_attempt_at is None or email_job.next_attempt_at <= now)
                and (email_job.locked_until is None or email_job.locked_until < now)
            )
            or (
                email_job.status == EmailJobStatus.PROCESSING
                and email_job.locked_until is not None
                and email_job.locked_until < now
            )
        ]

        if not available_jobs:
            return None

        email_job = sorted(
            available_jobs,
            key=lambda item: (
                item.next_attempt_at or datetime.min.replace(tzinfo=UTC),
                item.created_at,
                item.id,
            ),
        )[0]
        email_job.status = EmailJobStatus.PROCESSING
        email_job.locked_by = worker_id
        email_job.locked_until = now + lock_duration
        email_job.updated_at = now

        return email_job

    def claim_by_id(
        self,
        *,
        email_job_id: UUID,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        email_job = next(
            (job for job in self.email_jobs if job.id == email_job_id),
            None,
        )

        if email_job is None:
            return None

        if email_job.status == EmailJobStatus.SENT:
            return email_job

        eligible = (
            email_job.status == EmailJobStatus.PENDING
            and (email_job.next_attempt_at is None or email_job.next_attempt_at <= now)
            and (email_job.locked_until is None or email_job.locked_until < now)
        ) or (
            email_job.status == EmailJobStatus.PROCESSING
            and email_job.locked_until is not None
            and email_job.locked_until < now
        )

        if not eligible:
            return email_job

        email_job.status = EmailJobStatus.PROCESSING
        email_job.locked_by = worker_id
        email_job.locked_until = now + lock_duration
        email_job.updated_at = now

        return email_job

    def mark_sent(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
        provider_message_id: str | None = None,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.SENT
        email_job.sent_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.next_attempt_at = None
        if provider_message_id is not None:
            email_job.provider_message_id = provider_message_id
        email_job.updated_at = now

        return email_job

    def mark_failed(
        self,
        *,
        email_job: EmailJob,
        error: str,
        now: datetime,
        backoff_base_seconds: int,
        backoff_max_seconds: int,
    ) -> EmailJob:
        email_job.attempt_count += 1
        email_job.last_error = error
        email_job.locked_by = None
        email_job.locked_until = None

        if email_job.attempt_count >= email_job.max_attempts:
            email_job.status = EmailJobStatus.FAILED
            email_job.next_attempt_at = None
        else:
            backoff_seconds = calculate_email_job_backoff_seconds(
                email_job.attempt_count,
                backoff_base_seconds,
                backoff_max_seconds,
            )
            email_job.status = EmailJobStatus.PENDING
            email_job.next_attempt_at = now + timedelta(seconds=backoff_seconds)

        email_job.updated_at = now

        return email_job


def create_email_job(
    *,
    status: EmailJobStatus = EmailJobStatus.PENDING,
    attempt_count: int = 0,
    max_attempts: int = 3,
    locked_by: str | None = None,
    locked_until: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> EmailJob:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
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
        max_attempts=max_attempts,
        locked_by=locked_by,
        locked_until=locked_until,
        payload={},
        next_attempt_at=next_attempt_at,
        created_at=now,
        updated_at=now,
    )
