from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

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
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job_id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=1,
    )

    assert result.ack is True
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

    result = consumer.handle_delivery(
        body=b"not-json",
        delivery_tag=7,
    )

    assert result.ack is True
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
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=missing_job_id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=3,
    )

    assert result.ack is True


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
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result = consumer.handle_delivery(
        body=body,
        delivery_tag=5,
    )

    assert result.ack is True
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


def test_duplicate_rabbitmq_delivery_sends_email_only_once() -> None:
    email_job = create_email_job()
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

    first = consumer.handle_delivery(body=body, delivery_tag=1)
    second = consumer.handle_delivery(body=body, delivery_tag=2)

    assert first.ack is True
    assert second.ack is True
    assert len(provider.sent_messages) == 1
    assert email_job.status == EmailJobStatus.SENT


def test_already_sent_job_delivery_via_consumer_is_ignored() -> None:
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

    result = consumer.handle_delivery(body=body, delivery_tag=4)

    assert result.ack is True
    assert provider.sent_messages == []


@pytest.mark.parametrize(
    ("body",),
    [
        (b"{}",),
        (b'{"email_job_id": ""}',),
        (b'{"email_job_id": "not-a-uuid"}',),
        (b'{"type": "email_job_ready"}',),
    ],
)
def test_invalid_rabbitmq_payloads_are_acked_safely(body: bytes) -> None:
    repository = FakeEmailJobWorkerRepository([])
    worker = TrackingEmailJobWorkerService(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)

    result = consumer.handle_delivery(body=body, delivery_tag=9)

    assert result.ack is True
    assert worker.processed_job_ids == []
