from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.domain.jobs.email_job_retry import calculate_email_job_backoff_seconds
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.services.email_job_metrics import EmailJobOperationalMetrics, EmailJobStatusCounts
from app.services.email_job_pagination import EmailJobCursor


class SQLAlchemyEmailJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, email_job: EmailJob) -> EmailJob:
        self.session.add(email_job)
        self.session.flush()

        return email_job

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return self.session.get(EmailJob, email_job_id)

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        statement = (
            select(EmailJob)
            .where(
                EmailJob.job_type == job_type,
                EmailJob.idempotency_key == idempotency_key,
            )
            .limit(1)
        )

        return self.session.scalars(statement).first()

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
        statement = select(EmailJob)

        if cursor is not None:
            statement = statement.where(
                or_(
                    EmailJob.created_at < cursor.created_at,
                    and_(
                        EmailJob.created_at == cursor.created_at,
                        EmailJob.id < cursor.id,
                    ),
                ),
            )

        if job_type is not None:
            statement = statement.where(EmailJob.job_type == job_type)

        if status is not None:
            statement = statement.where(EmailJob.status == status)

        if appointment_id is not None:
            statement = statement.where(EmailJob.appointment_id == appointment_id)

        if patient_id is not None:
            statement = statement.where(EmailJob.patient_id == patient_id)

        statement = statement.order_by(
            desc(EmailJob.created_at),
            desc(EmailJob.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())

    def schedule_retry(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.next_attempt_at = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def reset_for_replay(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.attempt_count = 0
        email_job.next_attempt_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def get_operational_metrics(
        self,
        *,
        now: datetime,
    ) -> EmailJobOperationalMetrics:
        total_jobs = self._count_jobs()
        pending = self._count_jobs(EmailJob.status == EmailJobStatus.PENDING)
        processing = self._count_jobs(EmailJob.status == EmailJobStatus.PROCESSING)
        sent = self._count_jobs(EmailJob.status == EmailJobStatus.SENT)
        failed = self._count_jobs(EmailJob.status == EmailJobStatus.FAILED)
        dead_letter = self._count_jobs(EmailJob.status == EmailJobStatus.DEAD_LETTER)
        locked_count = self._count_jobs(
            EmailJob.locked_until.is_not(None),
            EmailJob.locked_until >= now,
        )
        expired_lock_count = self._count_jobs(
            EmailJob.status == EmailJobStatus.PROCESSING,
            EmailJob.locked_until.is_not(None),
            EmailJob.locked_until < now,
        )
        overdue_pending_count = self._count_jobs(
            EmailJob.status == EmailJobStatus.PENDING,
            or_(
                EmailJob.next_attempt_at.is_(None),
                EmailJob.next_attempt_at <= now,
            ),
        )
        oldest_pending_created_at = self.session.scalar(
            select(func.min(EmailJob.created_at)).where(
                EmailJob.status == EmailJobStatus.PENDING,
            ),
        )
        oldest_failed_created_at = self.session.scalar(
            select(func.min(EmailJob.created_at)).where(
                EmailJob.status == EmailJobStatus.FAILED,
            ),
        )
        newest_dead_letter_created_at = self.session.scalar(
            select(func.max(EmailJob.created_at)).where(
                EmailJob.status == EmailJobStatus.DEAD_LETTER,
            ),
        )

        return EmailJobOperationalMetrics(
            total_jobs=total_jobs,
            counts_by_status=EmailJobStatusCounts(
                pending=pending,
                processing=processing,
                sent=sent,
                failed=failed,
                dead_letter=dead_letter,
            ),
            locked_count=locked_count,
            expired_lock_count=expired_lock_count,
            overdue_pending_count=overdue_pending_count,
            oldest_pending_created_at=oldest_pending_created_at,
            oldest_failed_created_at=oldest_failed_created_at,
            newest_dead_letter_created_at=newest_dead_letter_created_at,
        )

    def _count_jobs(self, *conditions: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(EmailJob)

        if conditions:
            statement = statement.where(*conditions)

        return int(self.session.scalar(statement) or 0)

    def claim_next_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        statement = (
            select(EmailJob)
            .where(
                or_(
                    and_(
                        EmailJob.status == EmailJobStatus.PENDING,
                        or_(
                            EmailJob.next_attempt_at.is_(None),
                            EmailJob.next_attempt_at <= now,
                        ),
                        or_(
                            EmailJob.locked_until.is_(None),
                            EmailJob.locked_until < now,
                        ),
                    ),
                    and_(
                        EmailJob.status == EmailJobStatus.PROCESSING,
                        EmailJob.locked_until.is_not(None),
                        EmailJob.locked_until < now,
                    ),
                ),
            )
            .order_by(
                EmailJob.next_attempt_at.asc().nullsfirst(),
                EmailJob.created_at.asc(),
                EmailJob.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        email_job = self.session.scalars(statement).first()

        if email_job is None:
            return None

        email_job.status = EmailJobStatus.PROCESSING
        email_job.locked_by = worker_id
        email_job.locked_until = now + lock_duration
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def claim_by_id(
        self,
        *,
        email_job_id: UUID,
        worker_id: str,
        now: datetime,
        lock_duration: timedelta,
    ) -> EmailJob | None:
        statement = select(EmailJob).where(EmailJob.id == email_job_id).with_for_update()
        email_job = self.session.scalars(statement).first()

        if email_job is None:
            return None

        if email_job.status == EmailJobStatus.SENT:
            return email_job

        if not self._is_eligible_for_claim(email_job, now):
            return email_job

        email_job.status = EmailJobStatus.PROCESSING
        email_job.locked_by = worker_id
        email_job.locked_until = now + lock_duration
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def _is_eligible_for_claim(self, email_job: EmailJob, now: datetime) -> bool:
        if email_job.status == EmailJobStatus.PENDING:
            return (email_job.next_attempt_at is None or email_job.next_attempt_at <= now) and (
                email_job.locked_until is None or email_job.locked_until < now
            )

        if email_job.status == EmailJobStatus.PROCESSING:
            return email_job.locked_until is not None and email_job.locked_until < now

        return False

    def mark_sent(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
        provider_message_id: str | None = None,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.SENT
        email_job.sent_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.next_attempt_at = None
        if provider_message_id is not None:
            email_job.provider_message_id = provider_message_id
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def mark_failed(
        self,
        *,
        email_job: EmailJob,
        error: str,
        now: datetime,
        backoff_base_seconds: int,
        backoff_max_seconds: int,
    ) -> EmailJob:
        email_job.attempt_count += 1
        email_job.last_error = error
        email_job.locked_by = None
        email_job.locked_until = None

        if email_job.attempt_count >= email_job.max_attempts:
            email_job.status = EmailJobStatus.FAILED
            email_job.next_attempt_at = None
        else:
            backoff_seconds = calculate_email_job_backoff_seconds(
                email_job.attempt_count,
                backoff_base_seconds,
                backoff_max_seconds,
            )
            email_job.status = EmailJobStatus.PENDING
            email_job.next_attempt_at = now + timedelta(seconds=backoff_seconds)

        email_job.updated_at = now
        self.session.flush()

        return email_job
