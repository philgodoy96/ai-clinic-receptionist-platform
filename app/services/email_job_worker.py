from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.jobs.enums import EmailJobStatus
from app.models.email_jobs import EmailJob
from app.providers.email import EmailDeliveryProvider
from app.repositories.email_jobs import EmailJobWorkerRepository
from app.repositories.sqlalchemy.email_jobs import SQLAlchemyEmailJobRepository
from app.services.email_job_delivery_content import (
    UnsupportedEmailJobTypeError,
    build_email_delivery_message,
)

logger = logging.getLogger("app.email_jobs")

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class EmailJobWorkerResult:
    processed: bool
    job_id: UUID | None = None
    status: EmailJobStatus | None = None
    error: str | None = None
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ClaimResult:
    claimed: bool
    email_job: EmailJob | None = None
    skip_reason: str | None = None


class EmailJobWorkerService:
    def __init__(
        self,
        *,
        delivery_provider: EmailDeliveryProvider,
        worker_id: str,
        repository: EmailJobWorkerRepository | None = None,
        session_factory: Callable[[], Session] | None = None,
        repository_factory: Callable[[Session], EmailJobWorkerRepository] | None = None,
        lock_duration: timedelta = timedelta(minutes=5),
        backoff_base_seconds: int = 30,
        backoff_max_seconds: int = 900,
    ) -> None:
        if repository is None and session_factory is None:
            raise ValueError("repository or session_factory is required")
        if repository is not None and session_factory is not None:
            raise ValueError("repository and session_factory are mutually exclusive")

        self._repository = repository
        self._session_factory = session_factory
        self._repository_factory = repository_factory or (
            lambda session: SQLAlchemyEmailJobRepository(session)
        )
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
            claim_result = self._claim_next_available(now=current_time)
            if claim_result is None:
                break

            results.append(self._process_claimed_job(claim_result, now=current_time))

        return results

    def process_email_job(
        self,
        email_job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> EmailJobWorkerResult:
        current_time = now or datetime.now(UTC)
        claim_result = self._claim_by_id(email_job_id=email_job_id, now=current_time)

        if claim_result.skip_reason == "not_found":
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

        if claim_result.skip_reason == "already_sent":
            logger.info(
                "email_job_already_sent",
                extra={
                    "event": "email_job_already_sent",
                    "email_job_id": str(email_job_id),
                },
            )
            return EmailJobWorkerResult(
                processed=False,
                job_id=email_job_id,
                status=EmailJobStatus.SENT,
                skip_reason="already_sent",
            )

        if claim_result.skip_reason == "not_claimable":
            logger.info(
                "email_job_not_claimable",
                extra={
                    "event": "email_job_not_claimable",
                    "email_job_id": str(email_job_id),
                    "email_job_status": (
                        claim_result.email_job.status.value
                        if claim_result.email_job is not None
                        else None
                    ),
                },
            )
            return EmailJobWorkerResult(
                processed=False,
                job_id=email_job_id,
                status=(
                    claim_result.email_job.status if claim_result.email_job is not None else None
                ),
                skip_reason="not_claimable",
            )

        if not claim_result.claimed or claim_result.email_job is None:
            return EmailJobWorkerResult(processed=False, job_id=email_job_id)

        return self._process_claimed_job(claim_result.email_job, now=current_time)

    def _claim_next_available(self, *, now: datetime) -> EmailJob | None:
        def claim(repository: EmailJobWorkerRepository) -> EmailJob | None:
            return repository.claim_next_available(
                worker_id=self.worker_id,
                now=now,
                lock_duration=self.lock_duration,
            )

        if self._repository is not None:
            return claim(self._repository)

        return self._run_claim_transaction(claim)

    def _claim_by_id(self, *, email_job_id: UUID, now: datetime) -> _ClaimResult:
        def claim(repository: EmailJobWorkerRepository) -> _ClaimResult:
            email_job = repository.claim_by_id(
                email_job_id=email_job_id,
                worker_id=self.worker_id,
                now=now,
                lock_duration=self.lock_duration,
            )

            if email_job is None:
                return _ClaimResult(claimed=False, skip_reason="not_found")

            if email_job.status == EmailJobStatus.SENT:
                return _ClaimResult(
                    claimed=False,
                    email_job=email_job,
                    skip_reason="already_sent",
                )

            if (
                email_job.status != EmailJobStatus.PROCESSING
                or email_job.locked_by != self.worker_id
            ):
                return _ClaimResult(
                    claimed=False,
                    email_job=email_job,
                    skip_reason="not_claimable",
                )

            return _ClaimResult(claimed=True, email_job=email_job)

        if self._repository is not None:
            return claim(self._repository)

        return self._run_claim_transaction(claim)

    def _run_claim_transaction(self, claim: Callable[[EmailJobWorkerRepository], _T]) -> _T:
        session_factory = self._session_factory
        if session_factory is None:
            raise RuntimeError("session_factory is required for claim transactions")

        session = session_factory()
        try:
            repository = self._repository_factory(session)
            result = claim(repository)
            if isinstance(result, _ClaimResult) and not result.claimed:
                session.rollback()
                return result

            if result is None:
                session.rollback()
                return result

            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _run_finalize_transaction(
        self,
        finalize: Callable[[EmailJobWorkerRepository], EmailJobWorkerResult],
    ) -> EmailJobWorkerResult:
        if self._repository is not None:
            return finalize(self._repository)

        session_factory = self._session_factory
        if session_factory is None:
            raise RuntimeError("session_factory is required for finalize transactions")

        session = session_factory()
        try:
            repository = self._repository_factory(session)
            result = finalize(repository)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _process_claimed_job(
        self,
        email_job: EmailJob,
        *,
        now: datetime,
    ) -> EmailJobWorkerResult:
        job_id = email_job.id

        if email_job.recipient_email is None:
            return self._finalize_mark_failed(
                job_id=job_id,
                error="recipient_email_missing",
                now=now,
            )

        try:
            message = build_email_delivery_message(email_job)
        except UnsupportedEmailJobTypeError as exc:
            return self._finalize_mark_failed(job_id=job_id, error=str(exc), now=now)

        try:
            send_result = self.delivery_provider.send(message)
        except Exception as exc:
            return self._finalize_mark_failed(job_id=job_id, error=str(exc), now=now)

        return self._finalize_mark_sent(
            job_id=job_id,
            now=now,
            provider_message_id=send_result.provider_message_id,
        )

    def _finalize_mark_failed(
        self,
        *,
        job_id: UUID,
        error: str,
        now: datetime,
    ) -> EmailJobWorkerResult:
        def finalize(repository: EmailJobWorkerRepository) -> EmailJobWorkerResult:
            email_job = repository.get_by_id(job_id)
            if email_job is None:
                return EmailJobWorkerResult(
                    processed=False,
                    job_id=job_id,
                    skip_reason="not_found",
                )

            if not self._owns_processing_lock(email_job):
                return EmailJobWorkerResult(
                    processed=False,
                    job_id=job_id,
                    status=email_job.status,
                    skip_reason="not_claimable",
                )

            failed_job = repository.mark_failed(
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

        return self._run_finalize_transaction(finalize)

    def _finalize_mark_sent(
        self,
        *,
        job_id: UUID,
        now: datetime,
        provider_message_id: str | None,
    ) -> EmailJobWorkerResult:
        def finalize(repository: EmailJobWorkerRepository) -> EmailJobWorkerResult:
            email_job = repository.get_by_id(job_id)
            if email_job is None:
                return EmailJobWorkerResult(
                    processed=False,
                    job_id=job_id,
                    skip_reason="not_found",
                )

            if not self._owns_processing_lock(email_job):
                return EmailJobWorkerResult(
                    processed=False,
                    job_id=job_id,
                    status=email_job.status,
                    skip_reason="not_claimable",
                )

            sent_job = repository.mark_sent(
                email_job=email_job,
                now=now,
                provider_message_id=provider_message_id,
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

        return self._run_finalize_transaction(finalize)

    def _owns_processing_lock(self, email_job: EmailJob) -> bool:
        return (
            email_job.status == EmailJobStatus.PROCESSING and email_job.locked_by == self.worker_id
        )
