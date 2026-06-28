from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.domain.jobs.enums import EmailJobType
from app.email.idempotency import build_email_delivery_idempotency_key
from app.models.email_jobs import EmailJob
from app.providers.email import EmailMessage
from app.services.clinic_time import format_clinic_local_appointment_datetime

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
        subject, body = render_appointment_confirmation_content(email_job)
        return EmailMessage(
            to=email_job.recipient_email,
            subject=subject,
            body=body,
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


def render_appointment_confirmation_content(
    email_job: EmailJob,
    *,
    clinic_timezone: ZoneInfo | None = None,
) -> tuple[str, str]:
    payload = email_job.payload
    patient_name = _safe_text(payload.get("patient_name"))
    doctor_name = _safe_text(payload.get("doctor_name"))
    specialty_name = _safe_text(payload.get("specialty_name"))
    appointment_start_time = _parse_appointment_start_time(
        payload.get("appointment_start_time"),
    )
    timezone = clinic_timezone or ZoneInfo(get_settings().clinic_timezone)
    datetime_label = _format_appointment_datetime_label(
        appointment_start_time,
        timezone=timezone,
    )
    greeting = patient_name or "there"
    appointment_phrase = _build_appointment_phrase(
        specialty_name=specialty_name,
        doctor_name=doctor_name,
    )

    if _is_reschedule_confirmation(payload):
        subject = "Your appointment has been rescheduled"
        body = (
            f"Hi {greeting},\n\n"
            f"{appointment_phrase} has been rescheduled to {datetime_label}."
        )
        return subject, body

    subject = "Your appointment is confirmed"
    body = (
        f"Hi {greeting},\n\n"
        f"{appointment_phrase} is confirmed for {datetime_label}.\n\n"
        "Thank you."
    )
    return subject, body


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


def _build_appointment_phrase(
    *,
    specialty_name: str | None,
    doctor_name: str | None,
) -> str:
    clinician = doctor_name or "your clinician"
    if specialty_name is not None:
        return f"Your {specialty_name} appointment with {clinician}"
    return f"Your appointment with {clinician}"


def _format_appointment_datetime_label(
    appointment_start_time: datetime | None,
    *,
    timezone: ZoneInfo,
) -> str:
    if appointment_start_time is None:
        return "the scheduled time"

    return format_clinic_local_appointment_datetime(appointment_start_time, timezone)


def _is_reschedule_confirmation(payload: dict[str, Any]) -> bool:
    return _safe_text(payload.get("confirmation_reason")) == "reschedule"


def _parse_appointment_start_time(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


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
