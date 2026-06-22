from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.jobs.enums import EmailJobStatus
from app.providers.email import EmailDeliveryProvider
from app.repositories.email_jobs import EmailJobWorkerRepository
from app.services.email_job_delivery_content import (
    UnsupportedEmailJobTypeError,
    build_email_delivery_message,
)

logger = logging.getLogger("app.email_jobs")


@dataclass(frozen=True, slots=True)
class EmailJobWorkerResult:
    processed: bool
    job_id: UUID | None = None
    status: EmailJobStatus | None = None
    error: str | None = None


class EmailJobWorkerService:
    def __init__(
        self,
        *,
        repository: EmailJobWorkerRepository,
        delivery_provider: EmailDeliveryProvider,
        worker_id: str,
        lock_duration: timedelta = timedelta(minutes=5),
        backoff_base_seconds: int = 30,
        backoff_max_seconds: int = 900,
    ) -> None:
        self.repository = repository
        self.delivery_provider = delivery_provider
        self.worker_id = worker_id
        self.lock_duration = lock_duration
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds

    def process_one(self, *, now: datetime | None = None) -> EmailJobWorkerResult:
        current_time = now or datetime.now(UTC)
        email_job = self.repository.claim_next_available(
            worker_id=self.worker_id,
            now=current_time,
            lock_duration=self.lock_duration,
        )

        if email_job is None:
            return EmailJobWorkerResult(processed=False)

        if email_job.recipient_email is None:
            failed_job = self.repository.mark_failed(
                email_job=email_job,
                error="recipient_email_missing",
                now=current_time,
                backoff_base_seconds=self.backoff_base_seconds,
                backoff_max_seconds=self.backoff_max_seconds,
            )
            logger.info(
                "email_job_failed",
                extra={
                    "event": "email_job_failed",
                    "email_job_id": str(failed_job.id),
                    "email_job_status": failed_job.status.value,
                    "error": "recipient_email_missing",
                },
            )

            return EmailJobWorkerResult(
                processed=True,
                job_id=failed_job.id,
                status=failed_job.status,
                error="recipient_email_missing",
            )

        try:
            message = build_email_delivery_message(email_job)
        except UnsupportedEmailJobTypeError as exc:
            error = str(exc)
            failed_job = self.repository.mark_failed(
                email_job=email_job,
                error=error,
                now=current_time,
                backoff_base_seconds=self.backoff_base_seconds,
                backoff_max_seconds=self.backoff_max_seconds,
            )
            logger.info(
                "email_job_failed",
                extra={
                    "event": "email_job_failed",
                    "email_job_id": str(failed_job.id),
                    "email_job_status": failed_job.status.value,
                    "error": error,
                },
            )

            return EmailJobWorkerResult(
                processed=True,
                job_id=failed_job.id,
                status=failed_job.status,
                error=error,
            )

        try:
            send_result = self.delivery_provider.send(message)
        except Exception as exc:
            error = str(exc)
            failed_job = self.repository.mark_failed(
                email_job=email_job,
                error=error,
                now=current_time,
                backoff_base_seconds=self.backoff_base_seconds,
                backoff_max_seconds=self.backoff_max_seconds,
            )
            logger.info(
                "email_job_failed",
                extra={
                    "event": "email_job_failed",
                    "email_job_id": str(failed_job.id),
                    "email_job_status": failed_job.status.value,
                    "error": error,
                },
            )

            return EmailJobWorkerResult(
                processed=True,
                job_id=failed_job.id,
                status=failed_job.status,
                error=error,
            )

        sent_job = self.repository.mark_sent(
            email_job=email_job,
            now=current_time,
            provider_message_id=send_result.provider_message_id,
        )
        logger.info(
            "email_job_sent",
            extra={
                "event": "email_job_sent",
                "email_job_id": str(sent_job.id),
                "email_job_status": sent_job.status.value,
            },
        )

        return EmailJobWorkerResult(
            processed=True,
            job_id=sent_job.id,
            status=sent_job.status,
        )
