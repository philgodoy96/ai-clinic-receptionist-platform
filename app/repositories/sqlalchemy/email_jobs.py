from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
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
        email_job.scheduled_for = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now
        self.session.flush()

        return email_job

    def create_replay(
        self,
        *,
        original_email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        replay_job = EmailJob(
            job_type=original_email_job.job_type,
            status=EmailJobStatus.PENDING,
            appointment_id=original_email_job.appointment_id,
            patient_id=original_email_job.patient_id,
            recipient_email=original_email_job.recipient_email,
            subject=original_email_job.subject,
            body=original_email_job.body,
            attempts=0,
            max_attempts=original_email_job.max_attempts,
            locked_by=None,
            locked_until=None,
            last_error=None,
            payload={
                **original_email_job.payload,
                "replayed_from_email_job_id": str(original_email_job.id),
                "replayed_from_attempts": original_email_job.attempts,
                "replayed_from_status": original_email_job.status.value,
            },
            scheduled_for=now,
            sent_at=None,
            created_at=now,
            updated_at=now,
        )

        return self.add(replay_job)

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