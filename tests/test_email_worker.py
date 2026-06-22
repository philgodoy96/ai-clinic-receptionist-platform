from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.jobs.enums import EmailJobStatus
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_worker import EmailJobWorkerService
from tests.test_email_job_worker import FakeEmailJobWorkerRepository, create_email_job


def test_process_email_job_claims_specific_job() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    target_job = create_email_job()
    other_job = create_email_job()
    repository = FakeEmailJobWorkerRepository([other_job, target_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_email_job(target_job.id, now=now)

    assert result.processed is True
    assert result.job_id == target_job.id
    assert result.status == EmailJobStatus.SENT
    assert other_job.status == EmailJobStatus.PENDING
    assert len(provider.sent_messages) == 1


def test_process_due_email_jobs_processes_multiple_due_jobs() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    first_job = create_email_job()
    second_job = create_email_job()
    repository = FakeEmailJobWorkerRepository([second_job, first_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    results = worker.process_due_email_jobs(limit=2, now=now)

    assert len(results) == 2
    assert all(result.processed for result in results)
    assert len(provider.sent_messages) == 2


def test_process_email_job_skips_active_lock_from_another_worker() -> None:
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

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is False
    assert result.skip_reason == "not_claimable"
    assert provider.sent_messages == []


def test_process_email_job_returns_not_found_for_missing_job() -> None:
    repository = FakeEmailJobWorkerRepository([])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )

    result = worker.process_email_job(uuid4())

    assert result.processed is False
    assert result.skip_reason == "not_found"
