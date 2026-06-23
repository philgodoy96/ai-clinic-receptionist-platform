from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.appointment_rescheduling_enums import AppointmentRescheduleAttemptStatus
from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.repositories.appointments import RescheduleAttemptCreateResult


class SQLAlchemyAppointmentCancellationAttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        attempt: AppointmentCancellationAttempt,
    ) -> AppointmentCancellationAttempt:
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AppointmentCancellationAttempt | None:
        statement = (
            select(AppointmentCancellationAttempt)
            .where(AppointmentCancellationAttempt.idempotency_key == idempotency_key)
            .limit(1)
        )
        return self.session.scalars(statement).first()


class SQLAlchemyAppointmentRescheduleAttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AppointmentRescheduleAttempt | None:
        statement = (
            select(AppointmentRescheduleAttempt)
            .where(AppointmentRescheduleAttempt.idempotency_key == idempotency_key)
            .limit(1)
        )
        return self.session.scalars(statement).first()

    def create_attempt(
        self,
        *,
        idempotency_key: str,
        appointment_id: UUID,
    ) -> RescheduleAttemptCreateResult:
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return RescheduleAttemptCreateResult(attempt=existing, created=False)

        attempt = AppointmentRescheduleAttempt(
            idempotency_key=idempotency_key,
            appointment_id=appointment_id,
            status=AppointmentRescheduleAttemptStatus.PENDING,
        )

        try:
            self.session.add(attempt)
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raced = self.get_by_idempotency_key(idempotency_key)
            if raced is None:
                raise
            return RescheduleAttemptCreateResult(attempt=raced, created=False)

        return RescheduleAttemptCreateResult(attempt=attempt, created=True)

    def mark_succeeded(
        self,
        attempt: AppointmentRescheduleAttempt,
        *,
        new_appointment_id: UUID,
    ) -> AppointmentRescheduleAttempt:
        attempt.status = AppointmentRescheduleAttemptStatus.SUCCEEDED
        attempt.new_appointment_id = new_appointment_id
        attempt.error_code = None
        attempt.updated_at = datetime.now(UTC)
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def mark_failed_or_rejected(
        self,
        attempt: AppointmentRescheduleAttempt,
        *,
        error_code: str,
        status: AppointmentRescheduleAttemptStatus = (AppointmentRescheduleAttemptStatus.REJECTED),
    ) -> AppointmentRescheduleAttempt:
        attempt.status = status
        attempt.error_code = error_code
        attempt.updated_at = datetime.now(UTC)
        self.session.add(attempt)
        self.session.flush()
        return attempt
