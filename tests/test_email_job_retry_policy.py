from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.jobs.email_job_retry import calculate_email_job_backoff_seconds
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_metrics import EmailJobOperationalMetrics
from app.services.email_job_worker import EmailJobWorkerService
from app.services.email_jobs import (
    EmailJobService,
    InvalidEmailJobReplayStateError,
    InvalidEmailJobRetryStateError,
)


def test_calculate_email_job_backoff_seconds_uses_exponential_growth() -> None:
    assert calculate_email_job_backoff_seconds(1, 30, 900) == 30
    assert calculate_email_job_backoff_seconds(2, 30, 900) == 60
    assert calculate_email_job_backoff_seconds(3, 30, 900) == 120
    assert calculate_email_job_backoff_seconds(4, 30, 900) == 240


def test_calculate_email_job_backoff_seconds_caps_at_max() -> None:
    assert calculate_email_job_backoff_seconds(10, 30, 900) == 900


def test_worker_only_claims_due_pending_jobs() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    due_job = create_email_job(next_attempt_at=now - timedelta(minutes=1))
    future_job = create_email_job(next_attempt_at=now + timedelta(minutes=5))
    repository = RetryPolicyEmailJobWorkerRepository([due_job, future_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is True
    assert result.job_id == due_job.id
    assert due_job.status == EmailJobStatus.SENT
    assert future_job.status == EmailJobStatus.PENDING


def test_worker_does_not_claim_future_next_attempt_at() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    future_job = create_email_job(next_attempt_at=now + timedelta(minutes=5))
    repository = RetryPolicyEmailJobWorkerRepository([future_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert future_job.status == EmailJobStatus.PENDING


def test_worker_reclaims_expired_processing_lock() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    stuck_job = create_email_job(
        status=EmailJobStatus.PROCESSING,
        locked_by="worker-2",
        locked_until=now - timedelta(minutes=1),
    )
    repository = RetryPolicyEmailJobWorkerRepository([stuck_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.SENT
    assert stuck_job.locked_by is None
    assert stuck_job.sent_at == now


def test_failed_job_under_max_attempts_is_rescheduled() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(attempt_count=0, max_attempts=3)
    repository = RetryPolicyEmailJobWorkerRepository([email_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(should_fail=True),
        worker_id="worker-1",
        backoff_base_seconds=30,
        backoff_max_seconds=900,
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert email_job.last_error == "fake email provider failure"
    assert email_job.next_attempt_at == now + timedelta(seconds=30)
    assert email_job.locked_by is None
    assert email_job.locked_until is None


def test_failed_job_at_max_attempts_becomes_failed() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(attempt_count=2, max_attempts=3)
    repository = RetryPolicyEmailJobWorkerRepository([email_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(should_fail=True),
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.FAILED
    assert email_job.status == EmailJobStatus.FAILED
    assert email_job.attempt_count == 3
    assert email_job.next_attempt_at is None


def test_sent_job_is_not_retried_automatically() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    sent_job = create_email_job(status=EmailJobStatus.SENT, sent_at=now)
    repository = RetryPolicyEmailJobWorkerRepository([sent_job])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert sent_job.status == EmailJobStatus.SENT


def test_manual_replay_resets_failed_to_pending() -> None:
    failed_job = create_email_job(
        status=EmailJobStatus.FAILED,
        attempt_count=3,
        last_error="provider failure",
    )
    repository = RetryPolicyEmailJobRepository([failed_job])
    service = EmailJobService(repository=repository)
    replay_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    result = service.replay_failed_email_job(failed_job.id, now=replay_at)

    assert result.status == EmailJobStatus.PENDING
    assert result.attempt_count == 0
    assert result.next_attempt_at == replay_at
    assert result.last_error is None
    assert result.locked_by is None
    assert result.locked_until is None


def test_sent_job_cannot_be_replayed_accidentally() -> None:
    sent_job = create_email_job(status=EmailJobStatus.SENT, last_error=None, locked_by=None)
    repository = RetryPolicyEmailJobRepository([sent_job])
    service = EmailJobService(repository=repository)

    with pytest.raises(InvalidEmailJobReplayStateError):
        service.replay_failed_email_job(sent_job.id)


def test_manual_retry_resets_failed_to_pending_without_resetting_attempt_count() -> None:
    failed_job = create_email_job(
        status=EmailJobStatus.FAILED,
        attempt_count=2,
        last_error="provider failure",
    )
    repository = RetryPolicyEmailJobRepository([failed_job])
    service = EmailJobService(repository=repository)
    retry_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    result = service.retry_failed_email_job(failed_job.id, now=retry_at)

    assert result.status == EmailJobStatus.PENDING
    assert result.attempt_count == 2
    assert result.next_attempt_at == retry_at
    assert result.last_error == "provider failure"


def test_manual_retry_rejects_sent_job() -> None:
    sent_job = create_email_job(status=EmailJobStatus.SENT, last_error=None, locked_by=None)
    repository = RetryPolicyEmailJobRepository([sent_job])
    service = EmailJobService(repository=repository)

    with pytest.raises(InvalidEmailJobRetryStateError):
        service.retry_failed_email_job(sent_job.id)


class RetryPolicyEmailJobWorkerRepository:
    def __init__(self, email_jobs: list[EmailJob]) -> None:
        self.email_jobs = email_jobs

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
                and (
                    email_job.next_attempt_at is None
                    or email_job.next_attempt_at <= now
                )
                and (
                    email_job.locked_until is None
                    or email_job.locked_until < now
                )
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
            and (
                email_job.next_attempt_at is None
                or email_job.next_attempt_at <= now
            )
            and (
                email_job.locked_until is None
                or email_job.locked_until < now
            )
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


class RetryPolicyEmailJobRepository:
    def __init__(self, email_jobs: list[EmailJob]) -> None:
        self.email_jobs = email_jobs

    def add(self, email_job: EmailJob) -> EmailJob:
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

    def list_recent(self, **kwargs: object) -> list[EmailJob]:
        return self.email_jobs

    def schedule_retry(self, *, email_job: EmailJob, now: datetime) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.next_attempt_at = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        return email_job

    def reset_for_replay(self, *, email_job: EmailJob, now: datetime) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.attempt_count = 0
        email_job.next_attempt_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        return email_job

    def get_operational_metrics(self, *, now: datetime) -> EmailJobOperationalMetrics:
        raise NotImplementedError


def create_email_job(
    *,
    status: EmailJobStatus = EmailJobStatus.PENDING,
    attempt_count: int = 0,
    max_attempts: int = 3,
    locked_by: str | None = None,
    locked_until: datetime | None = None,
    next_attempt_at: datetime | None = None,
    sent_at: datetime | None = None,
    last_error: str | None = None,
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
        last_error=last_error,
        payload={},
        next_attempt_at=next_attempt_at,
        sent_at=sent_at,
        created_at=now,
        updated_at=now,
    )
