from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt


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
