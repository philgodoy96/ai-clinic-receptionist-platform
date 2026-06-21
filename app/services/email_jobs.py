from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.repositories.email_jobs import EmailJobRepository
from app.services.email_job_metrics import EmailJobOperationalMetrics
from app.services.email_job_pagination import (
    EmailJobCursor,
    decode_email_job_cursor,
    encode_email_job_cursor,
)


class EmailJobNotFoundError(LookupError):
    """Raised when an email job cannot be found."""


class InvalidEmailJobLimitError(ValueError):
    """Raised when an email job page size is invalid."""


class InvalidEmailJobRetryStateError(ValueError):
    """Raised when an email job cannot be manually retried."""


class InvalidEmailJobReplayStateError(ValueError):
    """Raised when an email job cannot be manually replayed."""


@dataclass(frozen=True, slots=True)
class EmailJobListFilters:
    job_type: EmailJobType | None = None
    status: EmailJobStatus | None = None
    appointment_id: UUID | None = None
    patient_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EmailJobListResult:
    items: Sequence[EmailJob]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AppointmentConfirmationEmailJobCreate:
    appointment_id: UUID
    patient_id: UUID
    recipient_email: str | None = None
    patient_name: str | None = None
    doctor_name: str | None = None
    appointment_start_time: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HumanEscalationNotificationEmailJobCreate:
    escalation_id: UUID
    conversation_id: UUID
    patient_id: UUID | None = None
    appointment_id: UUID | None = None
    recipient_email: str | None = None
    subject: str | None = None
    body: str | None = None
    summary: str | None = None
    reason: str | None = None
    priority: str | None = None
    source: str | None = None
    handoff_context: dict[str, Any] | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EmailJobService:
    def __init__(self, *, repository: EmailJobRepository) -> None:
        self.repository = repository

    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        subject = "Appointment confirmation"
        body = self._build_confirmation_body(payload)
        email_job = EmailJob(
            job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
            status=EmailJobStatus.PENDING,
            appointment_id=payload.appointment_id,
            patient_id=payload.patient_id,
            recipient_email=payload.recipient_email,
            subject=subject,
            body=body,
            attempts=0,
            max_attempts=3,
            payload={
                "patient_name": payload.patient_name,
                "doctor_name": payload.doctor_name,
                "appointment_start_time": payload.appointment_start_time,
                **payload.payload,
            },
        )

        return self.repository.add(email_job)

    def enqueue_human_escalation_notification(
        self,
        payload: HumanEscalationNotificationEmailJobCreate,
    ) -> EmailJob:
        subject = payload.subject or "Human escalation notification"
        body = payload.body or self._build_human_escalation_notification_body(payload)
        job_payload: dict[str, Any] = {
            "idempotency_key": payload.idempotency_key,
            "human_escalation_id": str(payload.escalation_id),
            "conversation_id": str(payload.conversation_id),
            "reason": payload.reason,
            "priority": payload.priority,
            "source": payload.source,
            "summary": payload.summary,
            "handoff_context": payload.handoff_context,
            **payload.payload,
        }

        if payload.patient_id is not None:
            job_payload["patient_id"] = str(payload.patient_id)

        if payload.appointment_id is not None:
            job_payload["appointment_id"] = str(payload.appointment_id)

        normalized_payload = {
            key: value for key, value in job_payload.items() if value is not None
        }
        email_job = EmailJob(
            job_type=EmailJobType.HUMAN_ESCALATION_NOTIFICATION,
            status=EmailJobStatus.PENDING,
            appointment_id=payload.appointment_id or payload.conversation_id,
            patient_id=payload.patient_id or payload.conversation_id,
            recipient_email=payload.recipient_email,
            subject=subject,
            body=body,
            attempts=0,
            max_attempts=3,
            payload=normalized_payload,
        )

        return self.repository.add(email_job)

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        return self.repository.get_by_idempotency_key(
            job_type=job_type,
            idempotency_key=idempotency_key,
        )

    def get_email_job(self, email_job_id: UUID) -> EmailJob:
        email_job = self.repository.get_by_id(email_job_id)

        if email_job is None:
            raise EmailJobNotFoundError(f"email job not found: {email_job_id}")

        return email_job

    def list_email_jobs(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        filters: EmailJobListFilters | None = None,
    ) -> EmailJobListResult:
        if limit < 1 or limit > 100:
            raise InvalidEmailJobLimitError("limit must be between 1 and 100")

        decoded_cursor = decode_email_job_cursor(cursor) if cursor is not None else None
        normalized_filters = filters or EmailJobListFilters()
        fetched_items = list(
            self.repository.list_recent(
                limit=limit + 1,
                cursor=decoded_cursor,
                job_type=normalized_filters.job_type,
                status=normalized_filters.status,
                appointment_id=normalized_filters.appointment_id,
                patient_id=normalized_filters.patient_id,
            ),
        )

        items = fetched_items[:limit]
        next_cursor = None

        if len(fetched_items) > limit and items:
            last_item = items[-1]
            next_cursor = encode_email_job_cursor(
                EmailJobCursor(
                    created_at=last_item.created_at,
                    id=last_item.id,
                ),
            )

        return EmailJobListResult(
            items=items,
            next_cursor=next_cursor,
        )

    def retry_failed_email_job(
        self,
        email_job_id: UUID,
        now: datetime | None = None,
    ) -> EmailJob:
        email_job = self.get_email_job(email_job_id)

        if email_job.status != EmailJobStatus.FAILED:
            raise InvalidEmailJobRetryStateError(
                f"email job cannot be retried from status: {email_job.status.value}",
            )

        effective_now = now if now is not None else datetime.now(UTC)

        return self.repository.schedule_retry(
            email_job=email_job,
            now=effective_now,
        )

    def replay_dead_letter_email_job(
        self,
        email_job_id: UUID,
        now: datetime | None = None,
    ) -> EmailJob:
        email_job = self.get_email_job(email_job_id)

        if email_job.status != EmailJobStatus.DEAD_LETTER:
            raise InvalidEmailJobReplayStateError(
                f"email job cannot be replayed from status: {email_job.status.value}",
            )

        effective_now = now if now is not None else datetime.now(UTC)

        return self.repository.create_replay(
            original_email_job=email_job,
            now=effective_now,
        )

    def get_operational_metrics(
        self,
        *,
        now: datetime | None = None,
    ) -> EmailJobOperationalMetrics:
        current_time = now or datetime.now(UTC)
        return self.repository.get_operational_metrics(now=current_time)

    def _build_confirmation_body(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> str:
        patient_name = payload.patient_name or "there"
        appointment_time = payload.appointment_start_time or "the scheduled time"
        doctor_name = payload.doctor_name or "your clinician"

        return (
            f"Hello {patient_name}, your appointment with {doctor_name} "
            f"has been confirmed for {appointment_time}."
        )

    def _build_human_escalation_notification_body(
        self,
        payload: HumanEscalationNotificationEmailJobCreate,
    ) -> str:
        summary = payload.summary or "A conversation requires staff attention."
        reason = payload.reason or "unknown"
        priority = payload.priority or "normal"

        return (
            f"Human escalation {payload.escalation_id} requires staff attention. "
            f"Reason: {reason}. Priority: {priority}. Summary: {summary}."
        )