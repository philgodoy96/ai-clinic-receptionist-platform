from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent


class SQLAlchemyVoiceCallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        statement = select(VoiceCall).where(
            VoiceCall.provider == provider,
            VoiceCall.provider_call_id == provider_call_id,
        )

        return self.session.scalars(statement).first()

    def create_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        self.session.add(voice_call)
        self.session.flush()

        return voice_call

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        existing = self.get_by_provider_call_id(
            provider=provider,
            provider_call_id=provider_call_id,
        )
        if existing is not None:
            return existing, False

        voice_call = VoiceCall(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        try:
            return self.create_voice_call(voice_call), True
        except IntegrityError:
            self.session.rollback()
            raced = self.get_by_provider_call_id(
                provider=provider,
                provider_call_id=provider_call_id,
            )
            if raced is None:
                raise

            return raced, False

    def update_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        self.session.add(voice_call)
        self.session.flush()

        return voice_call

    def create_voice_call_event(self, voice_call_event: VoiceCallEvent) -> VoiceCallEvent:
        self.session.add(voice_call_event)
        self.session.flush()

        return voice_call_event

    def get_event_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> VoiceCallEvent | None:
        statement = (
            select(VoiceCallEvent)
            .where(VoiceCallEvent.idempotency_key == idempotency_key)
            .limit(1)
        )

        return self.session.scalars(statement).first()

    def list_voice_calls(
        self,
        *,
        limit: int,
        status: VoiceCallStatus | None = None,
        provider: str | None = None,
    ) -> Sequence[VoiceCall]:
        statement = select(VoiceCall)

        if status is not None:
            statement = statement.where(VoiceCall.status == status)

        if provider is not None:
            statement = statement.where(VoiceCall.provider == provider)

        statement = statement.order_by(
            desc(VoiceCall.created_at),
            desc(VoiceCall.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())

    def list_events_for_call(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
    ) -> Sequence[VoiceCallEvent]:
        statement = (
            select(VoiceCallEvent)
            .where(VoiceCallEvent.voice_call_id == voice_call_id)
            .order_by(
                desc(VoiceCallEvent.occurred_at),
                desc(VoiceCallEvent.id),
            )
            .limit(limit)
        )

        return list(self.session.scalars(statement).all())
