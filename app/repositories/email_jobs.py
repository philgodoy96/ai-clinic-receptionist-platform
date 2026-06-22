from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_job_metrics import EmailJobOperationalMetrics
from app.services.email_job_pagination import EmailJobCursor


class EmailJobRepository(Protocol):
    def add(self, email_job: EmailJob) -> EmailJob:
        raise NotImplementedError

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        raise NotImplementedError

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        raise NotImplementedError

    def list_recent(
        self,
        *,
        limit: int,
        cursor: EmailJobCursor | None = None,
        job_type: EmailJobType | None = None,
        status: EmailJobStatus | None = None,
        appointment_id: UUID | None = None,
        patient_id: UUID | None = None,
    ) -> Sequence[EmailJob]:
        raise NotImplementedError

    def schedule_retry(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        raise NotImplementedError

    def reset_for_replay(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        raise NotImplementedError

    def get_operational_metrics(
        self,
        *,
        now: datetime,
    ) -> EmailJobOperationalMetrics:
        raise NotImplementedError


class EmailJobWorkerRepository(Protocol):
    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        raise NotImplementedError

    def claim_next_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        raise NotImplementedError

    def claim_by_id(
        self,
        *,
        email_job_id: UUID,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        raise NotImplementedError

    def mark_sent(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
        provider_message_id: str | None = None,
    ) -> EmailJob:
        raise NotImplementedError

    def mark_failed(
        self,
        *,
        email_job: EmailJob,
        error: str,
        now: datetime,
        backoff_base_seconds: int,
        backoff_max_seconds: int,
    ) -> EmailJob:
        raise NotImplementedError
