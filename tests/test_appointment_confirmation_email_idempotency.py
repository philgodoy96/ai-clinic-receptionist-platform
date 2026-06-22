from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.messaging.email_job_dispatch import InMemoryEmailJobDispatchPublisher
from app.models.email_jobs import EmailJob
from app.services.email_jobs import (
    APPOINTMENT_CONFIRMATION_IDEMPOTENCY_PREFIX,
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
    build_appointment_confirmation_idempotency_key,
)
from tests.test_email_jobs import FakeEmailJobRepository


def test_booking_creates_one_confirmation_email_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment_id,
        patient_id=uuid4(),
        appointment_start_time="2026-07-01T10:00:00+00:00",
        payload={"source": "scheduling_api"},
    )

    result = service.get_or_create_appointment_confirmation_email_job(payload)

    assert result.created is True
    assert len(repository.email_jobs) == 1
    assert result.email_job.idempotency_key == build_appointment_confirmation_idempotency_key(
        appointment_id,
    )


def test_repeated_enqueue_for_same_appointment_returns_same_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment_id,
        patient_id=uuid4(),
        appointment_start_time="2026-07-01T10:00:00+00:00",
        payload={"source": "chat_booking"},
    )

    first = service.get_or_create_appointment_confirmation_email_job(payload)
    second = service.get_or_create_appointment_confirmation_email_job(payload)

    assert first.created is True
    assert second.created is False
    assert second.email_job.id == first.email_job.id
    assert len(repository.email_jobs) == 1


def test_duplicate_rabbitmq_publish_does_not_create_duplicate_job() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    publisher = InMemoryEmailJobDispatchPublisher()
    appointment_id = uuid4()
    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment_id,
        patient_id=uuid4(),
        appointment_start_time="2026-07-01T10:00:00+00:00",
        payload={"source": "chat_booking"},
    )

    first = service.get_or_create_appointment_confirmation_email_job(payload)
    second = service.get_or_create_appointment_confirmation_email_job(payload)

    publisher.publish_email_job_ready(email_job_id=first.email_job.id)
    publisher.publish_email_job_ready(email_job_id=second.email_job.id)

    assert len(repository.email_jobs) == 1
    assert len(publisher.published_messages) == 2
    assert (
        publisher.published_messages[0].email_job_id
        == publisher.published_messages[1].email_job_id
    )


def test_sent_confirmation_job_is_not_recreated() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    sent_job = EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.SENT,
        appointment_id=appointment_id,
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="sent",
        attempt_count=1,
        max_attempts=3,
        idempotency_key=build_appointment_confirmation_idempotency_key(appointment_id),
        payload={"source": "chat_booking"},
        sent_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )
    repository.add(sent_job)

    result = service.get_or_create_appointment_confirmation_email_job(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=appointment_id,
            patient_id=sent_job.patient_id,
            appointment_start_time="2026-07-01T10:00:00+00:00",
        ),
    )

    assert result.created is False
    assert result.email_job.id == sent_job.id
    assert result.email_job.status == EmailJobStatus.SENT
    assert len(repository.email_jobs) == 1


def test_failed_confirmation_job_is_not_duplicated() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    failed_job = EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.FAILED,
        appointment_id=appointment_id,
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="failed",
        attempt_count=3,
        max_attempts=3,
        idempotency_key=build_appointment_confirmation_idempotency_key(appointment_id),
        last_error="provider failure",
        payload={"source": "chat_booking"},
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )
    repository.add(failed_job)

    result = service.get_or_create_appointment_confirmation_email_job(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=appointment_id,
            patient_id=failed_job.patient_id,
            appointment_start_time="2026-07-01T10:00:00+00:00",
        ),
    )

    assert result.created is False
    assert result.email_job.id == failed_job.id
    assert result.email_job.status == EmailJobStatus.FAILED
    assert len(repository.email_jobs) == 1


def test_failed_confirmation_job_can_be_manually_retried() -> None:
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    failed_job = EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.FAILED,
        appointment_id=appointment_id,
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="failed",
        attempt_count=3,
        max_attempts=3,
        idempotency_key=build_appointment_confirmation_idempotency_key(appointment_id),
        last_error="provider failure",
        payload={"source": "chat_booking"},
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )
    repository.add(failed_job)
    retry_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    retried = service.retry_failed_email_job(failed_job.id, now=retry_at)

    assert retried.status == EmailJobStatus.PENDING
    assert retried.attempt_count == 3
    assert len(repository.email_jobs) == 1


def test_idempotency_key_unique_constraint_prevents_race_duplicate() -> None:
    repository = RacingFakeEmailJobRepository()
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
    assert second.email_job.id == first.email_job.id
    assert len(repository.email_jobs) == 1


def test_idempotency_key_format_uses_appointment_confirmation_prefix() -> None:
    appointment_id = uuid4()

    assert build_appointment_confirmation_idempotency_key(appointment_id) == (
        f"{APPOINTMENT_CONFIRMATION_IDEMPOTENCY_PREFIX}:{appointment_id}"
    )


def test_quota_is_not_incremented_when_returning_existing_job() -> None:
    from app.services.clock import FixedClock
    from app.services.demo_guardrails import DemoGuardrailService
    from tests.demo_guardrail_support import (
        FIXED_GUARD_RAIL_NOW,
        FakeRedisClient,
        make_guardrail_settings,
    )

    redis_client = FakeRedisClient()
    guardrails = DemoGuardrailService(
        redis_client=redis_client,
        settings=make_guardrail_settings(DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP=5),
        clock=FixedClock(FIXED_GUARD_RAIL_NOW),
    )
    repository = FakeEmailJobRepository()
    service = EmailJobService(repository=repository)
    appointment_id = uuid4()
    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment_id,
        patient_id=uuid4(),
        appointment_start_time="2026-07-01T10:00:00+00:00",
        payload={"source": "chat_booking"},
    )
    client_ip = "203.0.113.10"

    guardrails.check_confirmation_email_allowed(client_ip)
    first = service.get_or_create_appointment_confirmation_email_job(payload)
    guardrails.record_confirmation_email_created(client_ip)
    email_keys_after_first = [key for key in redis_client.values if ":email:" in key]

    existing = service.get_by_idempotency_key(
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        idempotency_key=build_appointment_confirmation_idempotency_key(appointment_id),
    )
    assert existing is not None
    second = service.get_or_create_appointment_confirmation_email_job(payload)

    assert first.created is True
    assert second.created is False
    assert [key for key in redis_client.values if ":email:" in key] == email_keys_after_first


class RacingFakeEmailJobRepository(FakeEmailJobRepository):
    def add(self, email_job: EmailJob) -> EmailJob:
        if email_job.id is None:
            email_job.id = uuid4()

        if email_job.idempotency_key is not None:
            for existing_job in self.email_jobs:
                if (
                    existing_job.job_type == email_job.job_type
                    and existing_job.idempotency_key == email_job.idempotency_key
                ):
                    raise IntegrityError(
                        "duplicate idempotency key",
                        {},
                        Exception("duplicate idempotency key"),
                    )

        self.email_jobs.append(email_job)
        return email_job
