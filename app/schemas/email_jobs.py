from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.jobs.enums import EmailJobStatus, EmailJobType


class EmailJobResponse(BaseModel):
    id: UUID
    job_type: EmailJobType
    status: EmailJobStatus
    appointment_id: UUID
    patient_id: UUID
    recipient_email: str | None
    subject: str
    body: str
    attempt_count: int
    max_attempts: int
    locked_by: str | None
    locked_until: datetime | None
    last_error: str | None
    idempotency_key: str | None
    provider_message_id: str | None
    payload: dict[str, Any]
    next_attempt_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmailJobListResponse(BaseModel):
    items: list[EmailJobResponse]
    next_cursor: str | None
