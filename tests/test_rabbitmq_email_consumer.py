from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.jobs.enums import EmailJobStatus
from app.messaging.email_job_consumer import EmailJobRabbitMQConsumer
from app.messaging.email_job_dispatch import (
    EmailJobDispatchMessage,
    encode_email_job_dispatch_message,
)
from app.providers.email import EmailDeliveryProvider, FakeEmailDeliveryProvider
from app.repositories.email_jobs import EmailJobWorkerRepository
from app.services.email_job_worker import EmailJobWorkerResult, EmailJobWorkerService
from tests.test_email_job_worker import FakeEmailJobWorkerRepository, create_email_job


class FakeAcknowledger:
    def __init__(self) -> None:
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []

    def ack(self, *, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)

    def nack(self, *, delivery_tag: int, requeue: bool = False) -> None:
        self.nacked.append((delivery_tag, requeue))


class TrackingEmailJobWorkerService(EmailJobWorkerService):
    def __init__(
        self,
        *,
        repository: EmailJobWorkerRepository,
        delivery_provider: EmailDeliveryProvider,
        worker_id: str,
        lock_duration: timedelta = timedelta(minutes=5),
        backoff_base_seconds: int = 30,
        backoff_max_seconds: int = 900,
    ) -> None:
        super().__init__(
            repository=repository,
            delivery_provider=delivery_provider,
            worker_id=worker_id,
            lock_duration=lock_duration,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
        )
        self.processed_job_ids: list[UUID] = []

    def process_email_job(
        self,
        email_job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> EmailJobWorkerResult:
        self.processed_job_ids.append(email_job_id)
        return super().process_email_job(email_job_id, now=now)


def test_valid_rabbitmq_message_invokes_process_email_job() -> None:
    email_job_id = uuid4()
    repository = FakeEmailJobWorkerRepository([])
    worker = TrackingEmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    acknowledger = FakeAcknowledger()
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job_id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=1,
        acknowledger=acknowledger,
    )

    assert result.ack is True
    assert acknowledger.acked == [1]
    assert worker.processed_job_ids == [email_job_id]


def test_duplicate_message_for_sent_job_does_not_send_again() -> None:
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

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is False
    assert result.skip_reason == "already_sent"
    assert provider.sent_messages == []


def test_invalid_payload_is_acked_without_crashing_consumer() -> None:
    repository = FakeEmailJobWorkerRepository([])
    worker = TrackingEmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    acknowledger = FakeAcknowledger()

    result = consumer.handle_delivery(
        body=b"not-json",
        delivery_tag=7,
        acknowledger=acknowledger,
    )

    assert result.ack is True
    assert acknowledger.acked == [7]
    assert worker.processed_job_ids == []


def test_missing_job_is_acked_safely() -> None:
    missing_job_id = uuid4()
    repository = FakeEmailJobWorkerRepository([])
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    acknowledger = FakeAcknowledger()
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=missing_job_id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=3,
        acknowledger=acknowledger,
    )

    assert result.ack is True
    assert acknowledger.acked == [3]


def test_provider_failure_updates_job_retry_state_and_consumer_acks() -> None:
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
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    acknowledger = FakeAcknowledger()
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=5,
        acknowledger=acknowledger,
    )

    assert result.ack is True
    assert acknowledger.nacked == []
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert email_job.next_attempt_at is not None


def test_expired_lock_can_be_reclaimed_via_process_email_job() -> None:
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

    result = worker.process_email_job(email_job.id, now=now)

    assert result.processed is True
    assert result.status == EmailJobStatus.SENT
    assert email_job.status == EmailJobStatus.SENT
    assert len(provider.sent_messages) == 1


def test_dispatch_message_payload_contains_only_email_job_id() -> None:
    email_job_id = uuid4()
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job_id),
    )

    assert json.loads(body.decode("utf-8")) == {
        "email_job_id": str(email_job_id),
    }
