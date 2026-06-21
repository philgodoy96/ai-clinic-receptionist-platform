from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

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

REQUIRED_PAYLOAD_KEYS = (
    "human_escalation_id",
    "conversation_id",
    "reason",
    "priority",
    "source",
    "summary",
    "handoff_context",
)
FORBIDDEN_PAYLOAD_KEYS = (
    "raw_message",
    "user_message",
    "patient_identity",
    "raw_prompt",
    "raw_output",
    "transcript",
    "llm_shadow_analysis",
)
DEMO_STAFF_EMAIL = "clinic-staff@example.test"


def test_create_or_get_notification_job_creates_email_job_for_escalation() -> None:
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
    assert email_job.subject == "[Demo] Human escalation: user_requested_human"
    assert str(escalation.id) in email_job.body


def test_create_or_get_notification_job_is_idempotent_for_same_escalation() -> None:
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


def test_notification_payload_includes_required_operational_fields() -> None:
    repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=repository)
    service = HumanHandoffNotificationService(email_jobs=email_jobs)
    escalation = create_escalation(
        handoff_context={
            "active_hold_present": True,
            "selected_doctor_name": "Dr. Emily Carter",
            "requested_date": "2026-07-02",
            "selected_start_time": "2026-07-02T09:00:00+00:00",
        },
    )

    service.create_or_get_notification_job(escalation=escalation)

    payload = repository.email_jobs[0].payload
    for key in REQUIRED_PAYLOAD_KEYS:
        assert key in payload

    assert payload["human_escalation_id"] == str(escalation.id)
    assert payload["conversation_id"] == str(escalation.conversation_id)
    assert payload["reason"] == escalation.reason.value
    assert payload["priority"] == escalation.priority.value
    assert payload["source"] == escalation.source.value
    assert payload["summary"] == escalation.summary
    assert payload["handoff_context"] == escalation.handoff_context
    assert payload["idempotency_key"] == (
        f"{HUMAN_ESCALATION_NOTIFICATION_IDEMPOTENCY_PREFIX}:{escalation.id}"
    )


def test_notification_payload_excludes_forbidden_content_fields() -> None:
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
    email_job = repository.email_jobs[0]

    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in payload

    handoff_context = payload.get("handoff_context")
    if isinstance(handoff_context, dict):
        for key in FORBIDDEN_PAYLOAD_KEYS:
            assert key not in handoff_context

    lowered_body = email_job.body.lower()
    assert "patient_identity" not in lowered_body
    assert "raw_prompt" not in lowered_body
    assert "raw_output" not in lowered_body


def test_recipient_defaults_to_demo_staff_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("HUMAN_ESCALATION_NOTIFICATION_EMAIL", raising=False)

    try:
        assert get_settings().human_escalation_notification_email == DEMO_STAFF_EMAIL

        repository = FakeEmailJobRepository()
        email_jobs = EmailJobService(repository=repository)
        service = HumanHandoffNotificationService(email_jobs=email_jobs)

        service.create_or_get_notification_job(escalation=create_escalation())

        assert repository.email_jobs[0].recipient_email == DEMO_STAFF_EMAIL
    finally:
        get_settings.cache_clear()


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
