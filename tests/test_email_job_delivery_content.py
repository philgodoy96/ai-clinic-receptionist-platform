from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.providers.email import FakeEmailDeliveryProvider
from app.services.email_job_delivery_content import (
    build_email_delivery_message,
    render_appointment_confirmation_content,
    render_human_escalation_notification_content,
)
from app.services.email_job_worker import EmailJobWorkerService
from app.services.email_jobs import build_appointment_confirmation_idempotency_key
from tests.test_email_job_worker import FakeEmailJobWorkerRepository

NEW_YORK = ZoneInfo("America/New_York")
SUMMER_10_ET_UTC_ISO = "2026-06-29T14:00:00+00:00"


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


def test_appointment_confirmation_worker_renders_delivery_content() -> None:
    """Guardrail: appointment confirmation jobs render clinic-local content at delivery."""
    from tests.test_email_job_worker import create_email_job

    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment_id = uuid4()
    email_job = create_email_job(
        payload={
            "patient_name": "John Miller",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Dermatology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )
    email_job.appointment_id = appointment_id
    email_job.idempotency_key = build_appointment_confirmation_idempotency_key(appointment_id)
    repository = FakeEmailJobWorkerRepository([email_job])
    provider = FakeEmailDeliveryProvider()
    worker = EmailJobWorkerService(
        repository=repository,
        delivery_provider=provider,
        worker_id="worker-1",
    )

    result = worker.process_one(now=now)

    assert result.status == EmailJobStatus.SENT
    sent_message = provider.sent_messages[0]
    assert sent_message.subject == "Your appointment is confirmed"
    assert sent_message.body.startswith("Hi John Miller,")
    assert "Dermatology" in sent_message.body
    assert "Dr. Emily Carter" in sent_message.body
    assert "Monday, June 29 at 10:00" in sent_message.body
    assert SUMMER_10_ET_UTC_ISO not in sent_message.body
    assert sent_message.idempotency_key == f"appointment_confirmation:{appointment_id}"


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
        attempt_count=0,
        max_attempts=3,
        payload={},
        next_attempt_at=now,
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

    assert result.status == EmailJobStatus.PENDING
    assert email_job.attempt_count == 1
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
        attempt_count=0,
        max_attempts=3,
        payload=payload,
        created_at=now,
        updated_at=now,
    )


def create_appointment_confirmation_email_job(
    *,
    now: datetime | None = None,
    appointment_id: UUID | None = None,
    payload: dict[str, object] | None = None,
) -> EmailJob:
    effective_now = now or datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    effective_appointment_id = appointment_id or uuid4()
    return EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.PENDING,
        appointment_id=effective_appointment_id,
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Your appointment is confirmed",
        body="Pending delivery render.",
        attempt_count=0,
        max_attempts=3,
        idempotency_key=build_appointment_confirmation_idempotency_key(
            effective_appointment_id,
        ),
        payload=payload or {},
        created_at=effective_now,
        updated_at=effective_now,
    )


def test_appointment_confirmation_body_uses_clinic_local_time() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert "Monday, June 29 at 10:00" in body


def test_appointment_confirmation_body_excludes_raw_utc_iso_timestamp() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert SUMMER_10_ET_UTC_ISO not in body
    assert SUMMER_10_ET_UTC_ISO not in subject


def test_appointment_confirmation_uses_patient_name() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert subject == "Your appointment is confirmed"
    assert body.startswith("Hi Jane Doe,")


def test_appointment_confirmation_uses_doctor_name() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert "Dr. Emily Carter" in body


def test_appointment_confirmation_uses_specialty_name() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert "Your Cardiology appointment with Dr. Emily Carter" in body


def test_appointment_confirmation_missing_patient_name_falls_back_to_safe_greeting() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert body.startswith("Hi there,")


def test_appointment_confirmation_missing_doctor_and_specialty_fall_back_gracefully() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert "Your appointment with your clinician" in body


def test_appointment_confirmation_missing_specialty_only_uses_generic_appointment_phrase() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    _subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert "Your appointment with Dr. Emily Carter" in body
    assert "Dermatology" not in body


def test_reschedule_confirmation_reason_renders_reschedule_copy() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": "2026-06-30T19:00:00+00:00",
            "confirmation_reason": "reschedule",
        },
    )

    subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert subject == "Your appointment has been rescheduled"
    assert "has been rescheduled to Tuesday, June 30 at 15:00." in body
    assert "Thank you." not in body


def test_generic_confirmation_copy_when_no_reschedule_reason() -> None:
    email_job = create_appointment_confirmation_email_job(
        payload={
            "patient_name": "Jane Doe",
            "doctor_name": "Dr. Emily Carter",
            "specialty_name": "Cardiology",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
            "source": "chat",
        },
    )

    subject, body = render_appointment_confirmation_content(
        email_job,
        clinic_timezone=NEW_YORK,
    )

    assert subject == "Your appointment is confirmed"
    assert "is confirmed for" in body
    assert "has been rescheduled" not in body


def test_build_email_delivery_message_includes_idempotency_key_for_confirmation() -> None:
    appointment_id = uuid4()
    email_job = create_appointment_confirmation_email_job(
        appointment_id=appointment_id,
        payload={
            "patient_name": "Jane Doe",
            "appointment_start_time": SUMMER_10_ET_UTC_ISO,
        },
    )

    message = build_email_delivery_message(email_job)

    assert message.to == "patient@example.test"
    assert message.subject == "Your appointment is confirmed"
    assert "Jane Doe" in message.body
    assert message.idempotency_key == f"appointment_confirmation:{appointment_id}"
