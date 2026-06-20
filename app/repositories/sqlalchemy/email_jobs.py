from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.jobs.enums import EmailJobStatus
from app.models.email_jobs import EmailJob


class SQLAlchemyEmailJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, email_job: EmailJob) -> EmailJob:
        self.session.add(email_job)
        self.session.flush()

        return email_job

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
                EmailJob.status.in_(
                    [
                        EmailJobStatus.PENDING,
                        EmailJobStatus.FAILED,
                    ],
                ),
                EmailJob.scheduled_for <= now,
                EmailJob.attempts < EmailJob.max_attempts,
                or_(
                    EmailJob.locked_until.is_(None),
                    EmailJob.locked_until < now,
                ),
            )
            .order_by(
                EmailJob.scheduled_for.asc(),
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
        email_job.attempts += 1
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def mark_sent(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.SENT
        email_job.sent_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def mark_failed(
        self,
        *,
        email_job: EmailJob,
        error: str,
        now: datetime,
        retry_delay: timedelta,
    ) -> EmailJob:
        if email_job.attempts >= email_job.max_attempts:
            email_job.status = EmailJobStatus.DEAD_LETTER
        else:
            email_job.status = EmailJobStatus.FAILED
            email_job.scheduled_for = now + retry_delay

        email_job.last_error = error
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        self.session.flush()

        return email_job