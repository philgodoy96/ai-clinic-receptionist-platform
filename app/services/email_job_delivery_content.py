from __future__ import annotations

from typing import Any

from app.domain.jobs.enums import EmailJobType
from app.email.idempotency import build_email_delivery_idempotency_key
from app.models.email_jobs import EmailJob
from app.providers.email import EmailMessage

_HANDOFF_CONTEXT_BODY_KEYS = (
    ("active_hold_present", "Active hold present: yes"),
    ("selected_doctor_name", "Selected doctor: {value}"),
    ("requested_date", "Requested date: {value}"),
    ("selected_start_time", "Selected time: {value}"),
)


class UnsupportedEmailJobTypeError(ValueError):
    """Raised when the worker cannot render a delivery message for a job type."""


def build_email_delivery_message(email_job: EmailJob) -> EmailMessage:
    if email_job.recipient_email is None:
        raise ValueError("recipient_email_missing")

    if email_job.job_type == EmailJobType.APPOINTMENT_CONFIRMATION:
        return EmailMessage(
            to=email_job.recipient_email,
            subject=email_job.subject,
            body=email_job.body,
            idempotency_key=build_email_delivery_idempotency_key(email_job),
        )

    if email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION:
        subject, body = render_human_escalation_notification_content(email_job)
        return EmailMessage(
            to=email_job.recipient_email,
            subject=subject,
            body=body,
            idempotency_key=build_email_delivery_idempotency_key(email_job),
        )

    raise UnsupportedEmailJobTypeError(
        f"unsupported email job type: {str(email_job.job_type)}",
    )


def render_human_escalation_notification_content(
    email_job: EmailJob,
) -> tuple[str, str]:
    payload = email_job.payload
    reason = _safe_text(payload.get("reason")) or "unknown"
    subject = f"[Demo] Human escalation: {reason}"
    lines: list[str] = []

    escalation_id = payload.get("human_escalation_id") or payload.get("escalation_id")
    if escalation_id is not None:
        lines.append(f"Escalation ID: {escalation_id}")

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None:
        lines.append(f"Conversation ID: {conversation_id}")

    lines.append(f"Reason: {reason}")
    lines.append(f"Priority: {_safe_text(payload.get('priority')) or 'normal'}")
    lines.append(f"Source: {_safe_text(payload.get('source')) or 'unknown'}")

    summary = _safe_text(payload.get("summary"))
    if summary is not None:
        lines.append(f"Summary: {summary}")

    handoff_context = payload.get("handoff_context")
    if isinstance(handoff_context, dict):
        lines.extend(_render_handoff_context_lines(handoff_context))

    return subject, "\n".join(lines)


def _render_handoff_context_lines(handoff_context: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    for key, template in _HANDOFF_CONTEXT_BODY_KEYS:
        value = handoff_context.get(key)
        if value is None or value == "":
            continue

        if key == "active_hold_present":
            if value is True:
                lines.append(template)
            continue

        lines.append(template.format(value=value))

    return lines


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        return str(value)

    normalized = value.strip()
    return normalized or None
