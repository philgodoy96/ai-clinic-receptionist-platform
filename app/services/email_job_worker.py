from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.jobs.enums import EmailJobStatus
from app.models.email_jobs import EmailJob
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
    skip_reason: str | None = None


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
        results = self.process_due_email_jobs(limit=1, now=now)
        if not results:
            return EmailJobWorkerResult(processed=False)

        return results[0]

    def process_due_email_jobs(
        self,
        *,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[EmailJobWorkerResult]:
        if limit < 1:
            return []

        current_time = now or datetime.now(UTC)
        results: list[EmailJobWorkerResult] = []

        for _ in range(limit):
            email_job = self.repository.claim_next_available(
                worker_id=self.worker_id,
                now=current_time,
                lock_duration=self.lock_duration,
            )

            if email_job is None:
                break

            results.append(self._process_claimed_job(email_job, now=current_time))

        return results

    def process_email_job(
        self,
        email_job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> EmailJobWorkerResult:
        current_time = now or datetime.now(UTC)
        email_job = self.repository.claim_by_id(
            email_job_id=email_job_id,
            worker_id=self.worker_id,
            now=current_time,
            lock_duration=self.lock_duration,
        )

        if email_job is None:
            logger.warning(
                "email_job_not_found",
                extra={
                    "event": "email_job_not_found",
                    "email_job_id": str(email_job_id),
                },
            )
            return EmailJobWorkerResult(
                processed=False,
                job_id=email_job_id,
                skip_reason="not_found",
            )

        if email_job.status == EmailJobStatus.SENT:
            logger.info(
                "email_job_already_sent",
                extra={
                    "event": "email_job_already_sent",
                    "email_job_id": str(email_job_id),
                },
            )
            return EmailJobWorkerResult(
                processed=False,
                job_id=email_job.id,
                status=EmailJobStatus.SENT,
                skip_reason="already_sent",
            )

        if (
            email_job.status != EmailJobStatus.PROCESSING
            or email_job.locked_by != self.worker_id
        ):
            logger.info(
                "email_job_not_claimable",
                extra={
                    "event": "email_job_not_claimable",
                    "email_job_id": str(email_job_id),
                    "email_job_status": email_job.status.value,
                },
            )
            return EmailJobWorkerResult(
                processed=False,
                job_id=email_job.id,
                status=email_job.status,
                skip_reason="not_claimable",
            )

        return self._process_claimed_job(email_job, now=current_time)

    def _process_claimed_job(
        self,
        email_job: EmailJob,
        *,
        now: datetime,
    ) -> EmailJobWorkerResult:
        if email_job.recipient_email is None:
            failed_job = self.repository.mark_failed(
                email_job=email_job,
                error="recipient_email_missing",
                now=now,
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
                now=now,
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
                now=now,
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
            now=now,
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
