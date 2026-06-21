from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_delivery_content import (
    render_human_escalation_notification_content,
)
from app.services.email_job_worker import EmailJobWorkerService
from tests.test_email_job_worker import FakeEmailJobWorkerRepository


def test_human_escalation_notification_job_is_processed_as_sent_by_fake_worker() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    escalation_id = uuid4()
    conversation_id = uuid4()
    email_job = create_human_escalation_email_job(
        now=now,
        escalation_id=escalation_id,
        conversation_id=conversation_id,
    )
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.processed is True
    assert result.status == EmailJobStatus.SENT
    assert email_job.status == EmailJobStatus.SENT
    assert len(provider.sent_messages) == 1

    sent_message = provider.sent_messages[0]
    assert sent_message.to == "clinic-staff@example.test"
    assert sent_message.subject == "[Demo] Human escalation: user_requested_human"
    assert str(escalation_id) in sent_message.body
    assert str(conversation_id) in sent_message.body
    assert "Reason: user_requested_human" in sent_message.body
    assert "Priority: high" in sent_message.body
    assert "Active hold present: yes" in sent_message.body
    assert "Selected doctor: Dr. Emily Carter" in sent_message.body


def test_worker_rendering_excludes_patient_identity_from_delivery_body() -> None:
    email_job = create_human_escalation_email_job(
        now=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        escalation_id=uuid4(),
        conversation_id=uuid4(),
        extra_payload={
            "patient_name": "Jane Doe",
            "patient_email": "jane.doe@example.test",
            "raw_message": "Can I speak to a human?",
            "raw_prompt": "system: you are a bot",
            "raw_output": '{"intent":"human"}',
            "patient_identity": "Jane Doe, jane.doe@example.test",
        },
        extra_handoff_context={
            "patient_identity": "Jane Doe, jane.doe@example.test",
        },
    )

    _subject, body = render_human_escalation_notification_content(email_job)

    assert "Jane Doe" not in body
    assert "jane.doe@example.test" not in body
    assert "Can I speak to a human?" not in body
    assert "patient_identity" not in body
    assert "raw_prompt" not in body
    assert "raw_output" not in body


def test_appointment_confirmation_worker_tests_remain_supported() -> None:
    """Guardrail: human escalation worker support must not break confirmation jobs."""
    from tests.test_email_job_worker import create_email_job

    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = create_email_job(now=now)
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.SENT
    assert provider.sent_messages[0].subject == "Appointment confirmation"
    assert provider.sent_messages[0].body == "Your appointment is confirmed."


def test_unknown_email_job_type_fails_with_existing_retry_behavior() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    email_job = EmailJob(
        id=uuid4(),
        job_type=cast(EmailJobType, "unsupported_type"),
        status=EmailJobStatus.PENDING,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="ops@example.test",
        subject="Unsupported",
        body="Unsupported",
        attempts=0,
        max_attempts=3,
        payload={},
        scheduled_for=now,
        created_at=now,
        updated_at=now,
    )
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.FAILED
    assert email_job.last_error == "unsupported email job type: unsupported_type"
    assert provider.sent_messages == []


def create_human_escalation_email_job(
    *,
    now: datetime,
    escalation_id: UUID,
    conversation_id: UUID,
    extra_payload: dict[str, object] | None = None,
    extra_handoff_context: dict[str, object] | None = None,
) -> EmailJob:
    handoff_context = {
        "active_hold_present": True,
        "selected_doctor_name": "Dr. Emily Carter",
        "requested_date": "2026-07-02",
        "selected_start_time": "2026-07-02T09:00:00+00:00",
        **(extra_handoff_context or {}),
    }
    payload = {
        "human_escalation_id": str(escalation_id),
        "conversation_id": str(conversation_id),
        "reason": "user_requested_human",
        "priority": "high",
        "source": "chat",
        "summary": "User requested human receptionist handoff.",
        "handoff_context": handoff_context,
        **(extra_payload or {}),
    }

    return EmailJob(
        id=uuid4(),
        job_type=EmailJobType.HUMAN_ESCALATION_NOTIFICATION,
        status=EmailJobStatus.PENDING,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="clinic-staff@example.test",
        subject="placeholder subject",
        body="placeholder body",
        attempts=0,
        max_attempts=3,
        payload=payload,
        scheduled_for=now,
        created_at=now,
        updated_at=now,
    )
