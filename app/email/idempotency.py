from __future__ import annotations

import hashlib
from uuid import UUID

from app.models.email_jobs import EmailJob

RESEND_IDEMPOTENCY_KEY_MAX_LENGTH = 256


def build_email_delivery_idempotency_key(email_job: EmailJob) -> str:
    if email_job.idempotency_key is not None:
        normalized = normalize_resend_idempotency_key(email_job.idempotency_key)
        if normalized is not None:
            return normalized

    return normalize_resend_idempotency_key(f"email_job:{email_job.id}") or (
        f"email_job:{email_job.id}"
    )


def normalize_resend_idempotency_key(key: str) -> str | None:
    normalized = key.strip()
    if not normalized:
        return None

    if len(normalized) <= RESEND_IDEMPOTENCY_KEY_MAX_LENGTH:
        return normalized

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


def build_appointment_confirmation_resend_idempotency_key(appointment_id: UUID) -> str:
    return f"appointment_confirmation:{appointment_id}"
