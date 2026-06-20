from datetime import datetime, timedelta
from typing import Protocol

from app.models.email_jobs import EmailJob


class EmailJobRepository(Protocol):
    def add(self, email_job: EmailJob) -> EmailJob:
        raise NotImplementedError


class EmailJobWorkerRepository(Protocol):
    def claim_next_available(
        self,
        *,
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
    ) -> EmailJob:
        raise NotImplementedError

    def mark_failed(
        self,
        *,
        email_job: EmailJob,
        error: str,
        now: datetime,
        retry_delay: timedelta,
    ) -> EmailJob:
        raise NotImplementedError