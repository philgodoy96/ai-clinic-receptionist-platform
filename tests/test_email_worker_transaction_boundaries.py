from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.jobs.enums import EmailJobStatus
from app.email.types import EmailSendResult, OutboundEmailMessage
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.repositories.email_jobs import EmailJobWorkerRepository
from app.services.email_job_worker import EmailJobWorkerService
from tests.test_email_job_worker import FakeEmailJobWorkerRepository, create_email_job


@dataclass
class TransactionTrackingStore:
    jobs: list[EmailJob]
    claim_commits: int = 0
    finalize_commits: int = 0
    provider_send_after_claim_commit: list[bool] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


class TransactionTrackingSession:
    def __init__(self, store: TransactionTrackingStore) -> None:
        self._store = store
        self.repository = TransactionBoundaryRepository(store, self)
        self._claimed_in_session = False

    def commit(self) -> None:
        if self._claimed_in_session:
            self._store.claim_commits += 1
            self._store.events.append("claim_committed")
        else:
            self._store.finalize_commits += 1
            self._store.events.append("finalize_committed")

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def mark_claimed(self) -> None:
        self._claimed_in_session = True


class TransactionBoundaryRepository(FakeEmailJobWorkerRepository):
    def __init__(
        self,
        store: TransactionTrackingStore,
        session: TransactionTrackingSession,
    ) -> None:
        super().__init__(store.jobs)
        self._session = session

    def claim_next_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        email_job = super().claim_next_available(
            worker_id=worker_id,
            now=now,
            lock_duration=lock_duration,
        )
        if email_job is not None:
            self._session.mark_claimed()
        return email_job

    def claim_by_id(
        self,
        *,
        email_job_id: UUID,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        email_job = super().claim_by_id(
            email_job_id=email_job_id,
            worker_id=worker_id,
            now=now,
            lock_duration=lock_duration,
        )
        if (
            email_job is not None
            and email_job.status == EmailJobStatus.PROCESSING
            and email_job.locked_by == worker_id
        ):
            self._session.mark_claimed()
        return email_job


class ClaimCommitAwareProvider:
    def __init__(
        self,
        *,
        inner: FakeEmailDeliveryProvider,
        store: TransactionTrackingStore,
    ) -> None:
        self._inner = inner
        self._store = store

    def send(self, message: OutboundEmailMessage) -> EmailSendResult:
        claim_committed = self._store.claim_commits > 0
        self._store.provider_send_after_claim_commit.append(claim_committed)
        self._store.events.append("provider_send")
        if not claim_committed:
            raise AssertionError("provider send called before claim commit")
        return self._inner.send(message)


def build_transactional_worker(
    *,
    store: TransactionTrackingStore,
    provider: ClaimCommitAwareProvider,
    worker_id: str = "worker-1",
    backoff_base_seconds: int = 30,
    backoff_max_seconds: int = 900,
) -> EmailJobWorkerService:
    def session_factory() -> Session:
        return cast(Session, TransactionTrackingSession(store))

    def repository_factory(session: Session) -> EmailJobWorkerRepository:
        assert isinstance(session, TransactionTrackingSession)
        return session.repository

    return EmailJobWorkerService(
        session_factory=session_factory,
        repository_factory=repository_factory,
        delivery_provider=provider,
        worker_id=worker_id,
        backoff_base_seconds=backoff_base_seconds,
        backoff_max_seconds=backoff_max_seconds,
    )


def test_provider_send_is_not_called_when_claim_fails() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(
        locked_by="worker-2",
        locked_until=now + timedelta(minutes=5),
    )
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is False
    assert result.skip_reason == "not_claimable"
    assert inner_provider.sent_messages == []
    assert store.claim_commits == 0
    assert store.finalize_commits == 0
    assert store.events == []


def test_claim_state_is_persisted_before_provider_send() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is True
    assert result.status == EmailJobStatus.SENT
    assert store.claim_commits == 1
    assert store.provider_send_after_claim_commit == [True]
    assert store.events.index("claim_committed") < store.events.index("provider_send")


def test_provider_send_happens_outside_claim_transaction() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    worker.process_email_job(email_job.id, now=now)

    assert store.claim_commits == 1
    assert store.finalize_commits == 1
    assert store.events == [
        "claim_committed",
        "provider_send",
        "finalize_committed",
    ]


def test_success_marks_job_sent_in_second_transaction() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.status == EmailJobStatus.SENT
    assert email_job.status == EmailJobStatus.SENT
    assert email_job.sent_at == now
    assert email_job.locked_by is None
    assert email_job.locked_until is None
    assert store.finalize_commits == 1


def test_provider_failure_schedules_retry_in_second_transaction() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider(should_fail=True)
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is True
    assert result.status == EmailJobStatus.PENDING
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert email_job.next_attempt_at == now + timedelta(seconds=30)
    assert email_job.last_error == "fake email provider failure"
    assert email_job.locked_by is None
    assert store.claim_commits == 1
    assert store.finalize_commits == 1


def test_provider_failure_marks_failed_when_attempts_exhausted() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(attempt_count=2, max_attempts=3)
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider(should_fail=True)
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.status == EmailJobStatus.FAILED
    assert email_job.status == EmailJobStatus.FAILED
    assert email_job.attempt_count == 3
    assert store.finalize_commits == 1


def test_duplicate_already_sent_job_is_ignored() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(status=EmailJobStatus.SENT)
    email_job.sent_at = now
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is False
    assert result.skip_reason == "already_sent"
    assert inner_provider.sent_messages == []
    assert store.claim_commits == 0
    assert store.finalize_commits == 0
