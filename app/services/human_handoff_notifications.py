from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from app.core.config import get_settings
from app.domain.jobs.enums import EmailJobType
from app.models.human_escalation import HumanEscalation
from app.services.email_jobs import (
    EmailJobService,
    HumanEscalationNotificationEmailJobCreate,
)

HUMAN_ESCALATION_NOTIFICATION_IDEMPOTENCY_PREFIX = "human_escalation_notification"


@dataclass(frozen=True, slots=True)
class HumanHandoffNotificationResult:
    email_job_id: UUID
    created: bool


class HumanHandoffNotificationService:
    def __init__(self, *, email_jobs: EmailJobService) -> None:
        self.email_jobs = email_jobs

    def create_or_get_notification_job(
        self,
        *,
        escalation: HumanEscalation,
    ) -> HumanHandoffNotificationResult:
        idempotency_key = self._build_idempotency_key(escalation.id)
        existing = self.email_jobs.get_by_idempotency_key(
            job_type=EmailJobType.HUMAN_ESCALATION_NOTIFICATION,
            idempotency_key=idempotency_key,
        )

        if existing is not None:
            return HumanHandoffNotificationResult(
                email_job_id=existing.id,
                created=False,
            )

        settings = get_settings()
        email_job = self.email_jobs.enqueue_human_escalation_notification(
            HumanEscalationNotificationEmailJobCreate(
                escalation_id=escalation.id,
                conversation_id=escalation.conversation_id,
                patient_id=escalation.patient_id,
                appointment_id=escalation.appointment_id,
                recipient_email=settings.human_escalation_notification_email,
                subject=self._build_subject(escalation),
                body=self._build_body(escalation),
                summary=escalation.summary,
                reason=escalation.reason.value,
                priority=escalation.priority.value,
                source=escalation.source.value,
                handoff_context=escalation.handoff_context,
                idempotency_key=idempotency_key,
            ),
        )

        return HumanHandoffNotificationResult(
            email_job_id=email_job.id,
            created=True,
        )

    def _build_idempotency_key(self, escalation_id: UUID) -> str:
        return f"{HUMAN_ESCALATION_NOTIFICATION_IDEMPOTENCY_PREFIX}:{escalation_id}"

    def _build_subject(self, escalation: HumanEscalation) -> str:
        return f"[Demo] Human escalation: {escalation.reason.value}"

    def _build_body(self, escalation: HumanEscalation) -> str:
        lines = [
            f"Human escalation {escalation.id} requires staff attention.",
            f"Reason: {escalation.reason.value}",
            f"Priority: {escalation.priority.value}",
            f"Source: {escalation.source.value}",
            f"Conversation ID: {escalation.conversation_id}",
        ]

        if escalation.patient_id is not None:
            lines.append(f"Patient ID: {escalation.patient_id}")

        if escalation.appointment_id is not None:
            lines.append(f"Appointment ID: {escalation.appointment_id}")

        if escalation.summary is not None:
            lines.append(f"Summary: {escalation.summary}")

        if escalation.handoff_context:
            lines.append(
                f"Handoff context: {json.dumps(escalation.handoff_context, sort_keys=True)}",
            )

        return "\n".join(lines)
