from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voice_booking_attempt import VoiceBookingAttempt


class SQLAlchemyVoiceBookingAttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def update(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get_by_idempotency_key(self, idempotency_key: str) -> VoiceBookingAttempt | None:
        statement = (
            select(VoiceBookingAttempt)
            .where(VoiceBookingAttempt.idempotency_key == idempotency_key)
            .limit(1)
        )
        return self.session.scalars(statement).first()

    def get_by_id(self, attempt_id: UUID) -> VoiceBookingAttempt | None:
        return self.session.get(VoiceBookingAttempt, attempt_id)
