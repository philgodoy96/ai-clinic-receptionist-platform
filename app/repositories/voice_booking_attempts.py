from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.voice_booking_attempt import VoiceBookingAttempt


class VoiceBookingAttemptRepository(Protocol):
    def add(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        raise NotImplementedError

    def update(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        raise NotImplementedError

    def get_by_idempotency_key(self, idempotency_key: str) -> VoiceBookingAttempt | None:
        raise NotImplementedError

    def get_by_id(self, attempt_id: UUID) -> VoiceBookingAttempt | None:
        raise NotImplementedError
