"""Focused safety contract tests for EmailJob delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.jobs.enums import EmailJobStatus
from app.email.resend_provider import ResendEmailProvider
from app.messaging.email_job_consumer import EmailJobRabbitMQConsumer
from app.messaging.email_job_dispatch import (
    EmailJobDispatchMessage,
    encode_email_job_dispatch_message,
)
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_worker import EmailJobWorkerResult, EmailJobWorkerService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
    build_appointment_confirmation_idempotency_key,
)
from tests.test_email_job_worker import FakeEmailJobWorkerRepository, create_email_job
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_email_worker_transaction_boundaries import (
    ClaimCommitAwareProvider,
    TransactionTrackingStore,
    build_transactional_worker,
)
from tests.test_rabbitmq_email_ack_ordering import (
    build_failing_finalize_worker,
    deliver_with_ack,
)
from tests.test_resend_email_provider import FakeResendEmailClient


def _build_tracked_worker(
    email_job: EmailJob,
    *,
    should_fail: bool = False,
    worker_id: str = "worker-1",
) -> tuple[EmailJobWorkerService, TransactionTrackingStore, FakeEmailDeliveryProvider]:
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider(should_fail=should_fail)
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider, worker_id=worker_id)
    return worker, store, inner_provider


# --- 1. Claim boundary ---


def test_polling_does_not_call_provider_when_claim_fails() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(
        locked_by="worker-2",
        locked_until=now + timedelta(minutes=5),
    )
    worker, store, inner_provider = _build_tracked_worker(email_job)

    result = worker.process_one(now=now)

    assert result.processed is False
    assert inner_provider.sent_messages == []
    assert store.claim_commits == 0


def test_polling_claim_is_persisted_before_provider_send() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    worker, store, _inner_provider = _build_tracked_worker(email_job)

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.SENT
    assert store.claim_commits == 1
    assert store.provider_send_after_claim_commit == [True]
    assert store.events.index("claim_committed") < store.events.index("provider_send")


def test_polling_provider_send_happens_outside_claim_transaction() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    worker, store, _inner_provider = _build_tracked_worker(email_job)

    worker.process_one(now=now)

    assert store.events == [
        "claim_committed",
        "provider_send",
        "finalize_committed",
    ]


def test_finalize_sent_state_persisted_after_provider_success() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    worker, store, _inner_provider = _build_tracked_worker(email_job)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.status == EmailJobStatus.SENT
    assert email_job.status == EmailJobStatus.SENT
    assert email_job.sent_at == now
    assert store.events.index("provider_send") < store.events.index("finalize_committed")
    assert store.finalize_commits == 1


# --- 2. Retry/failure boundary ---


def test_provider_exception_commits_retry_state_with_next_attempt_at() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job()
    worker, store, _inner_provider = _build_tracked_worker(email_job, should_fail=True)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
    assert email_job.next_attempt_at == now + timedelta(seconds=30)
    assert email_job.last_error == "fake email provider failure"
    assert store.finalize_commits == 1


def test_max_attempts_exhausted_marks_failed_in_finalize_transaction() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(attempt_count=2, max_attempts=3)
    worker, store, _inner_provider = _build_tracked_worker(email_job, should_fail=True)

    result = worker.process_email_job(email_job.id, now=now)

    assert result.status == EmailJobStatus.FAILED
    assert email_job.status == EmailJobStatus.FAILED
    assert email_job.next_attempt_at is None
    assert store.finalize_commits == 1


def test_failed_terminal_state_committed_before_rabbitmq_ack() -> None:
    email_job = create_email_job(attempt_count=2, max_attempts=3)
    store = TransactionTrackingStore(jobs=[email_job])
    inner_provider = FakeEmailDeliveryProvider(should_fail=True)
    provider = ClaimCommitAwareProvider(inner=inner_provider, store=store)
    worker = build_transactional_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    _result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=11,
        store=store,
    )

    assert email_job.status == EmailJobStatus.FAILED
    assert acknowledger.acked == [11]
    assert store.events.index("finalize_committed") < store.events.index("rabbitmq_acked")


# --- 3. RabbitMQ ---


def test_valid_rabbitmq_message_calls_durable_processor() -> None:
    email_job_id = uuid4()
    repository = FakeEmailJobWorkerRepository([])
    processed_ids: list[UUID] = []

    class TrackingWorker(EmailJobWorkerService):
        def process_email_job(
            self,
            email_job_id: UUID,
            *,
            now: datetime | None = None,
        ) -> EmailJobWorkerResult:
            processed_ids.append(email_job_id)
            return super().process_email_job(email_job_id, now=now)

    worker = TrackingWorker(
        repository=repository,
        delivery_provider=FakeEmailDeliveryProvider(),
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job_id),
    )

    result, _ack = deliver_with_ack(consumer, body=body, delivery_tag=12)

    assert result.ack is True
    assert processed_ids == [email_job_id]


def test_rabbitmq_ack_follows_durable_commit_on_success() -> None:
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    provider = ClaimCommitAwareProvider(
        inner=FakeEmailDeliveryProvider(),
        store=store,
    )
    worker = build_transactional_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    _result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=13,
        store=store,
    )

    assert acknowledger.acked == [13]
    assert store.events.index("finalize_committed") < store.events.index("rabbitmq_acked")


def test_rabbitmq_does_not_ack_before_commit_on_db_exception() -> None:
    email_job = create_email_job()
    store = TransactionTrackingStore(jobs=[email_job])
    provider = ClaimCommitAwareProvider(
        inner=FakeEmailDeliveryProvider(),
        store=store,
    )
    worker = build_failing_finalize_worker(store=store, provider=provider)
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    result, acknowledger = deliver_with_ack(
        consumer,
        body=body,
        delivery_tag=14,
        store=store,
    )

    assert result.ack is False
    assert result.requeue is True
    assert acknowledger.acked == []
    assert acknowledger.nacked == [(14, True)]


def test_duplicate_rabbitmq_message_after_sent_does_not_resend() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(status=EmailJobStatus.SENT)
    email_job.sent_at = now
    inner_provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([email_job]),
        delivery_provider=inner_provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=email_job.id),
    )

    deliver_with_ack(consumer, body=body, delivery_tag=15)
    deliver_with_ack(consumer, body=body, delivery_tag=16)

    assert inner_provider.sent_messages == []


def test_missing_job_rabbitmq_delivery_is_safe_no_op() -> None:
    missing_job_id = uuid4()
    inner_provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([]),
        delivery_provider=inner_provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)
    body = encode_email_job_dispatch_message(
        EmailJobDispatchMessage(email_job_id=missing_job_id),
    )

    result, acknowledger = deliver_with_ack(consumer, body=body, delivery_tag=17)

    assert result.ack is True
    assert acknowledger.acked == [17]
    assert inner_provider.sent_messages == []


@pytest.mark.parametrize("body", [b"not-json", b"{}", b'{"email_job_id": "not-a-uuid"}'])
def test_malformed_rabbitmq_payload_is_safe_no_op(body: bytes) -> None:
    inner_provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([]),
        delivery_provider=inner_provider,
        worker_id="worker-1",
    )
    consumer = EmailJobRabbitMQConsumer(worker=worker)

    result, acknowledger = deliver_with_ack(consumer, body=body, delivery_tag=18)

    assert result.ack is True
    assert acknowledger.acked == [18]
    assert inner_provider.sent_messages == []


# --- 4. Idempotency ---


def test_appointment_confirmation_idempotency_key_exists_on_enqueue() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()

    result = service.get_or_create_appointment_confirmation_email_job(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=appointment_id,
            patient_id=uuid4(),
            appointment_start_time="2026-07-01T10:00:00+00:00",
        ),
    )

    assert result.email_job.idempotency_key == (
        build_appointment_confirmation_idempotency_key(appointment_id)
    )


def test_resend_receives_appointment_confirmation_idempotency_key_from_worker() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment_id = uuid4()
    email_job = create_email_job()
    email_job.appointment_id = appointment_id
    email_job.idempotency_key = build_appointment_confirmation_idempotency_key(appointment_id)
    client = FakeResendEmailClient(response={"id": "resend-safe-1"})
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([email_job]),
        delivery_provider=ResendEmailProvider(
            client=client,
            from_address="sender@example.test",
        ),
        worker_id="worker-1",
    )

    worker.process_email_job(email_job.id, now=now)

    assert client.sent_idempotency_keys == [
        f"appointment_confirmation:{appointment_id}",
    ]
    assert email_job.provider_message_id == "resend-safe-1"


def test_repeated_enqueue_does_not_create_duplicate_confirmation_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment_id,
        patient_id=uuid4(),
        appointment_start_time="2026-07-01T10:00:00+00:00",
    )

    first = service.get_or_create_appointment_confirmation_email_job(payload)
    second = service.get_or_create_appointment_confirmation_email_job(payload)

    assert first.created is True
    assert second.created is False
    assert len(repository.email_jobs) == 1


def test_sent_confirmation_job_is_not_resent_automatically() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(status=EmailJobStatus.SENT)
    email_job.sent_at = now
    inner_provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([email_job]),
        delivery_provider=inner_provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert inner_provider.sent_messages == []


# --- 5. Polling + RabbitMQ convergence ---


def test_polling_and_rabbitmq_both_require_claim_before_provider_send() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    polling_job = create_email_job()
    polling_store = TransactionTrackingStore(jobs=[polling_job])
    polling_provider = ClaimCommitAwareProvider(
        inner=FakeEmailDeliveryProvider(),
        store=polling_store,
    )
    polling_worker = build_transactional_worker(
        store=polling_store,
        provider=polling_provider,
    )
    polling_worker.process_one(now=now)

    rabbit_job = create_email_job()
    rabbit_store = TransactionTrackingStore(jobs=[rabbit_job])
    rabbit_provider = ClaimCommitAwareProvider(
        inner=FakeEmailDeliveryProvider(),
        store=rabbit_store,
    )
    rabbit_worker = build_transactional_worker(
        store=rabbit_store,
        provider=rabbit_provider,
    )
    rabbit_worker.process_email_job(rabbit_job.id, now=now)

    assert polling_store.provider_send_after_claim_commit == [True]
    assert rabbit_store.provider_send_after_claim_commit == [True]
    assert polling_store.claim_commits == 1
    assert rabbit_store.claim_commits == 1


def test_polling_and_rabbitmq_share_finalize_transaction_semantics() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    polling_job = create_email_job()
    polling_store = TransactionTrackingStore(jobs=[polling_job])
    polling_worker = build_transactional_worker(
        store=polling_store,
        provider=ClaimCommitAwareProvider(
            inner=FakeEmailDeliveryProvider(),
            store=polling_store,
        ),
    )
    polling_worker.process_one(now=now)

    rabbit_job = create_email_job()
    rabbit_store = TransactionTrackingStore(jobs=[rabbit_job])
    rabbit_worker = build_transactional_worker(
        store=rabbit_store,
        provider=ClaimCommitAwareProvider(
            inner=FakeEmailDeliveryProvider(),
            store=rabbit_store,
        ),
    )
    rabbit_worker.process_email_job(rabbit_job.id, now=now)

    assert polling_store.events == [
        "claim_committed",
        "provider_send",
        "finalize_committed",
    ]
    assert rabbit_store.events == [
        "claim_committed",
        "provider_send",
        "finalize_committed",
    ]
    assert polling_job.status == EmailJobStatus.SENT
    assert rabbit_job.status == EmailJobStatus.SENT


def test_polling_skips_provider_when_claim_is_not_available() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    future_job = create_email_job(next_attempt_at=now + timedelta(minutes=5))
    inner_provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=FakeEmailJobWorkerRepository([future_job]),
        delivery_provider=inner_provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is False
    assert inner_provider.sent_messages == []
