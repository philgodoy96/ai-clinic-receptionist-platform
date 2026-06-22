from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domain.jobs.enums import EmailJobStatus
from app.messaging.email_job_consumer import (
    EmailJobConsumerHandleResult,
    EmailJobRabbitMQConsumer,
    apply_email_job_delivery_ack,
)
from app.messaging.email_job_dispatch import (
    EmailJobDispatchMessage,
    encode_email_job_dispatch_message,
)
from app.providers.email import FakeEmailDeliveryProvider
from app.repositories.email_jobs import EmailJobWorkerRepository
from app.services.email_job_worker import EmailJobWorkerService
from tests.test_email_job_worker import FakeEmailJobWorkerRepository, create_email_job
from tests.test_email_worker_transaction_boundaries import (
    ClaimCommitAwareProvider,
    TransactionBoundaryRepository,
    TransactionTrackingSession,
    TransactionTrackingStore,
    build_transactional_worker,
)


class FakeAcknowledger:
    def __init__(self, store: TransactionTrackingStore | None = None) -> None:
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []
        self._store = store

    def ack(self, *, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)
        if self._store is not None:
            self._store.events.append("rabbitmq_acked")

    def nack(self, *, delivery_tag: int, requeue: bool = False) -> None:
        self.nacked.append((delivery_tag, requeue))
        if self._store is not None:
            self._store.events.append("rabbitmq_nacked")


class FailingFinalizeTrackingSession(TransactionTrackingSession):
    def commit(self) -> None:
        if self._claimed_in_session:
            super().commit()
            return

        self._store.events.append("finalize_commit_attempted")
        raise RuntimeError("finalize commit failed")


def build_failing_finalize_worker(
    *,
    store: TransactionTrackingStore,
    provider: ClaimCommitAwareProvider,
    worker_id: str = "worker-1",
) -> EmailJobWorkerService:
    def session_factory() -> Session:
        return cast(Session, FailingFinalizeTrackingSession(store))

    def repository_factory(session: Session) -> EmailJobWorkerRepository:
        assert isinstance(session, FailingFinalizeTrackingSession)
        return TransactionBoundaryRepository(store, session)

    return EmailJobWorkerService(
        session_factory=session_factory,
        repository_factory=repository_factory,
        delivery_provider=provider,
        worker_id=worker_id,
    )


def deliver_with_ack(
    consumer: EmailJobRabbitMQConsumer,
    *,
    body: bytes,
    delivery_tag: int,
    store: TransactionTrackingStore | None = None,
) -> tuple[EmailJobConsumerHandleResult, FakeAcknowledger]:
    acknowledger = FakeAcknowledger(store)
    result = consumer.handle_delivery(body=body, delivery_tag=delivery_tag)
    apply_email_job_delivery_ack(
        result,
        delivery_tag=delivery_tag,
        acknowledger=acknowledger,
    )
    return result, acknowledger


def test_rabbitmq_ack_happens_after_final_commit() -> None:
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    _, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=1,
        store=store,
    )

    assert acknowledger.acked == [1]
    assert acknowledger.nacked == []
    assert store.finalize_commits == 1
    assert store.events.index("finalize_committed") < store.events.index("rabbitmq_acked")


def test_rabbitmq_ack_does_not_happen_on_db_error() -> None:
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider()
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_failing_finalize_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=2,
        store=store,
    )

    assert result.ack is False
    assert result.requeue is True
    assert acknowledger.acked == []
    assert acknowledger.nacked == [(2, True)]
    assert "rabbitmq_acked" not in store.events
    assert store.finalize_commits == 0
    assert store.events.count("finalize_commit_attempted") == 1


def test_provider_failure_commits_retry_state_before_ack() -> None:
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider(should_fail=True)
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=3,
        store=store,
    )

    assert result.ack is True
    assert acknowledger.acked == [3]
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert store.finalize_commits == 1
    assert store.events.index("finalize_committed") < store.events.index("rabbitmq_acked")


def test_duplicate_rabbitmq_message_for_already_sent_job_does_not_send_again() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(status=EmailJobStatus.SENT)
    email_job.sent_at = now
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    first, first_ack = deliver_with_ack(consumer, body=body, delivery_tag=4)
    second, second_ack = deliver_with_ack(consumer, body=body, delivery_tag=5)

    assert first.ack is True
    assert second.ack is True
    assert first_ack.acked == [4]
    assert second_ack.acked == [5]
    assert provider.sent_messages == []


def test_malformed_message_does_not_call_email_provider() -> None:
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([]),
        delivery_provider=provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)

    result, acknowledger = deliver_with_ack(consumer, body=b"not-json", delivery_tag=6)

    assert result.ack is True
    assert acknowledger.acked == [6]
    assert provider.sent_messages == []


@pytest.mark.parametrize(
    ("body",),
    [
        (b"{}",),
        (b'{"email_job_id": ""}',),
        (b'{"email_job_id": "not-a-uuid"}',),
    ],
)
def test_invalid_rabbitmq_payloads_do_not_call_email_provider(body: bytes) -> None:
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([]),
        delivery_provider=provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)

    result, acknowledger = deliver_with_ack(consumer, body=body, delivery_tag=7)

    assert result.ack is True
    assert acknowledger.acked == [7]
    assert provider.sent_messages == []


def test_missing_job_does_not_call_email_provider() -> None:
    missing_job_id = uuid4()
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([]),
        delivery_provider=provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=missing_job_id),
    )

    result, acknowledger = deliver_with_ack(consumer, body=body, delivery_tag=8)

    assert result.ack is True
    assert acknowledger.acked == [8]
    assert provider.sent_messages == []


def test_future_next_attempt_at_does_not_call_email_provider() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(next_attempt_at=now + timedelta(minutes=5))
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([email_job]),
        delivery_provider=provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=9,
    )

    assert result.ack is True
    assert acknowledger.acked == [9]
    assert provider.sent_messages == []
    assert email_job.status == EmailJobStatus.PENDING
