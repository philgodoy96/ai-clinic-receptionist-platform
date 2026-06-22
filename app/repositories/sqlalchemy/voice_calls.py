from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, asc, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.services.voice_call_pagination import VoiceCallCursor, VoiceCallEventCursor


class SQLAlchemyVoiceCallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, voice_call_id: UUID) -> VoiceCall | None:
        return self.session.get(VoiceCall, voice_call_id)

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

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        event = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)

        if event is None:
            return None

        metadata = event.event_metadata

        if not isinstance(metadata, dict):
            return None

        outcome = metadata.get("tool_call_outcome")

        if not isinstance(outcome, dict):
            return None

        return outcome

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        existing = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)

        if existing is not None:
            return False

        voice_call_event = VoiceCallEvent(
            voice_call_id=voice_call_id,
            provider=provider,
            provider_call_id=provider_call_id,
            provider_event_id=tool_call_id,
            event_type=event_type,
            occurred_at=occurred_at,
            event_metadata={"tool_call_outcome": outcome},
            idempotency_key=idempotency_key,
        )

        try:
            self.create_voice_call_event(voice_call_event)
        except IntegrityError:
            self.session.rollback()
            return False

        return True

    def list_voice_calls(
        self,
        *,
        limit: int,
        cursor: VoiceCallCursor | None = None,
        status: VoiceCallStatus | None = None,
        provider: str | None = None,
        provider_call_id: str | None = None,
        created_after: datetime | None = None,
    ) -> Sequence[VoiceCall]:
        statement = select(VoiceCall)

        if cursor is not None:
            statement = statement.where(
                or_(
                    VoiceCall.created_at < cursor.created_at,
                    and_(
                        VoiceCall.created_at == cursor.created_at,
                        VoiceCall.id < cursor.id,
                    ),
                ),
            )

        if status is not None:
            statement = statement.where(VoiceCall.status == status)

        if provider is not None:
            statement = statement.where(VoiceCall.provider == provider)

        if provider_call_id is not None:
            statement = statement.where(VoiceCall.provider_call_id == provider_call_id)

        if created_after is not None:
            statement = statement.where(VoiceCall.created_at >= created_after)

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
        cursor: VoiceCallEventCursor | None = None,
    ) -> Sequence[VoiceCallEvent]:
        statement = select(VoiceCallEvent).where(
            VoiceCallEvent.voice_call_id == voice_call_id,
        )

        if cursor is not None:
            statement = statement.where(
                or_(
                    VoiceCallEvent.occurred_at > cursor.occurred_at,
                    and_(
                        VoiceCallEvent.occurred_at == cursor.occurred_at,
                        VoiceCallEvent.id > cursor.id,
                    ),
                ),
            )

        statement = statement.order_by(
            asc(VoiceCallEvent.occurred_at),
            asc(VoiceCallEvent.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())
