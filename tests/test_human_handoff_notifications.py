from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import get_settings
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.human_escalation import HumanEscalation
from app.services.email_jobs import EmailJobService
from app.services.human_handoff_notifications import (
    HUMAN_ESCALATION_NOTIFICATION_IDEMPOTENCY_PREFIX,
    HumanHandoffNotificationService,
)
from tests.test_email_jobs import FakeEmailJobRepository


def test_create_or_get_notification_job_creates_pending_email_job() -> None:
    repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=repository)
    service = HumanHandoffNotificationService(email_jobs=email_jobs)
    escalation = create_escalation()

    result = service.create_or_get_notification_job(escalation=escalation)

    assert result.created is True
    assert len(repository.email_jobs) == 1

    email_job = repository.email_jobs[0]
    assert result.email_job_id == email_job.id
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert email_job.status == EmailJobStatus.PENDING
    assert email_job.recipient_email == get_settings().human_escalation_notification_email
    assert email_job.subject == "[Demo] Human escalation: user_requested_human"
    assert str(escalation.id) in email_job.body
    assert email_job.payload["human_escalation_id"] == str(escalation.id)
    assert email_job.payload["conversation_id"] == str(escalation.conversation_id)
    assert email_job.payload["patient_id"] == str(escalation.patient_id)
    assert email_job.payload["appointment_id"] == str(escalation.appointment_id)
    assert email_job.payload["reason"] == escalation.reason.value
    assert email_job.payload["priority"] == escalation.priority.value
    assert email_job.payload["source"] == escalation.source.value
    assert email_job.payload["summary"] == escalation.summary
    assert email_job.payload["handoff_context"] == escalation.handoff_context
    assert email_job.payload["idempotency_key"] == (
        f"{HUMAN_ESCALATION_NOTIFICATION_IDEMPOTENCY_PREFIX}:{escalation.id}"
    )


def test_create_or_get_notification_job_is_idempotent() -> None:
    repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=repository)
    service = HumanHandoffNotificationService(email_jobs=email_jobs)
    escalation = create_escalation()

    first = service.create_or_get_notification_job(escalation=escalation)
    second = service.create_or_get_notification_job(escalation=escalation)

    assert first.created is True
    assert second.created is False
    assert second.email_job_id == first.email_job_id
    assert len(repository.email_jobs) == 1


def test_notification_payload_excludes_patient_identity_fields() -> None:
    repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=repository)
    service = HumanHandoffNotificationService(email_jobs=email_jobs)
    escalation = create_escalation(
        handoff_context={
            "active_hold_present": True,
            "selected_doctor_name": "Dr. Emily Carter",
        },
    )

    service.create_or_get_notification_job(escalation=escalation)

    payload = repository.email_jobs[0].payload
    forbidden_keys = {
        "patient_name",
        "patient_email",
        "raw_message",
        "user_message",
        "transcript",
    }

    assert forbidden_keys.isdisjoint(payload.keys())
    assert "patient_name" not in repository.email_jobs[0].body.lower()
    assert "patient_email" not in repository.email_jobs[0].body.lower()


def create_escalation(
    *,
    handoff_context: dict[str, object] | None = None,
) -> HumanEscalation:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    return HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        patient_id=uuid4(),
        appointment_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        summary="User requested human receptionist handoff.",
        handoff_context=handoff_context
        or {
            "active_hold_present": True,
            "hold_id": "hold-123",
        },
        created_at=now,
        updated_at=now,
    )
