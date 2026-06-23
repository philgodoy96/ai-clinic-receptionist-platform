from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.domain.voice_calls.enums import NormalizedVoiceCallEventType
from app.domain.voice_calls.lifecycle import (
    build_safe_event_metadata,
    build_voice_call_event_idempotency_key,
    normalize_retell_event_type,
    redact_phone_number,
    should_apply_status_update,
    status_for_normalized_event,
)
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.repositories.voice_calls import VoiceCallRepository

DEFAULT_RETELL_PROVIDER = "retell"


class MissingProviderCallIdError(ValueError):
    """Raised when a Retell lifecycle payload omits provider_call_id."""


@dataclass(frozen=True, slots=True)
class RetellLifecyclePayload:
    provider_call_id: str
    event_type: str
    occurred_at: datetime
    provider_event_id: str | None = None
    sequence_number: int | None = None
    direction: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)
    provider: str = DEFAULT_RETELL_PROVIDER


@dataclass(frozen=True, slots=True)
class RetellLifecycleIngestionResult:
    voice_call: VoiceCall
    voice_call_event: VoiceCallEvent
    created_call: bool
    created_event: bool


class RetellCallLifecycleService:
    def __init__(self, *, repository: VoiceCallRepository) -> None:
        self.repository = repository

    def ingest_event(self, payload: RetellLifecyclePayload) -> RetellLifecycleIngestionResult:
        provider_call_id = payload.provider_call_id.strip()
        if not provider_call_id:
            msg = "provider_call_id is required"
            raise MissingProviderCallIdError(msg)

        normalized_event_type = normalize_retell_event_type(payload.event_type)
        idempotency_key = build_voice_call_event_idempotency_key(
            provider=payload.provider,
            provider_call_id=provider_call_id,
            event_type=payload.event_type,
            provider_event_id=payload.provider_event_id,
            sequence_number=payload.sequence_number,
            occurred_at=payload.occurred_at,
        )

        existing_event = self.repository.get_event_by_idempotency_key(
            idempotency_key=idempotency_key,
        )
        if existing_event is not None:
            voice_call = self.repository.get_by_provider_call_id(
                provider=payload.provider,
                provider_call_id=provider_call_id,
            )
            if voice_call is None:
                msg = "voice call for existing lifecycle event was not found"
                raise LookupError(msg)

            return RetellLifecycleIngestionResult(
                voice_call=voice_call,
                voice_call_event=existing_event,
                created_call=False,
                created_event=False,
            )

        voice_call, created_call = self.repository.get_or_create_voice_call(
            provider=payload.provider,
            provider_call_id=provider_call_id,
        )

        self._apply_voice_call_updates(
            voice_call=voice_call,
            payload=payload,
            normalized_event_type=normalized_event_type,
        )

        voice_call_event = VoiceCallEvent(
            voice_call_id=voice_call.id,
            provider=payload.provider,
            provider_call_id=provider_call_id,
            provider_event_id=payload.provider_event_id,
            event_type=payload.event_type,
            normalized_event_type=normalized_event_type,
            occurred_at=payload.occurred_at,
            sequence_number=payload.sequence_number,
            event_metadata=build_safe_event_metadata(payload.safe_metadata),
            idempotency_key=idempotency_key,
        )

        try:
            created_event = self.repository.create_voice_call_event(voice_call_event)
        except IntegrityError:
            raced_event = self.repository.get_event_by_idempotency_key(
                idempotency_key=idempotency_key,
            )
            if raced_event is None:
                raise

            raced_call = self.repository.get_by_provider_call_id(
                provider=payload.provider,
                provider_call_id=provider_call_id,
            )
            if raced_call is None:
                msg = "voice call for raced lifecycle event was not found"
                raise LookupError(msg) from None

            return RetellLifecycleIngestionResult(
                voice_call=raced_call,
                voice_call_event=raced_event,
                created_call=False,
                created_event=False,
            )

        self.repository.update_voice_call(voice_call)

        return RetellLifecycleIngestionResult(
            voice_call=voice_call,
            voice_call_event=created_event,
            created_call=created_call,
            created_event=True,
        )

    def _apply_voice_call_updates(
        self,
        *,
        voice_call: VoiceCall,
        payload: RetellLifecyclePayload,
        normalized_event_type: NormalizedVoiceCallEventType,
    ) -> None:
        previous_last_event_at = voice_call.last_event_at
        if voice_call.last_event_at is None or payload.occurred_at > voice_call.last_event_at:
            voice_call.last_event_at = payload.occurred_at

        if payload.direction is not None and voice_call.direction is None:
            voice_call.direction = payload.direction

        if payload.from_number is not None and voice_call.from_number_redacted is None:
            voice_call.from_number_redacted = redact_phone_number(payload.from_number)

        if payload.to_number is not None and voice_call.to_number_redacted is None:
            voice_call.to_number_redacted = redact_phone_number(payload.to_number)

        if normalized_event_type == NormalizedVoiceCallEventType.CALL_STARTED:
            if voice_call.started_at is None or payload.occurred_at < voice_call.started_at:
                voice_call.started_at = payload.occurred_at

        if normalized_event_type in (
            NormalizedVoiceCallEventType.CALL_ENDED,
            NormalizedVoiceCallEventType.CALL_FAILED,
        ):
            if voice_call.ended_at is None or payload.occurred_at > voice_call.ended_at:
                voice_call.ended_at = payload.occurred_at

        proposed_status = status_for_normalized_event(normalized_event_type)
        if (
            should_apply_status_update(
                current_status=voice_call.status,
                proposed_status=proposed_status,
                event_occurred_at=payload.occurred_at,
                previous_last_event_at=previous_last_event_at,
            )
            and proposed_status is not None
        ):
            voice_call.status = proposed_status

        voice_call.updated_at = datetime.now(UTC)
