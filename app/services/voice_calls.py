from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.repositories.voice_calls import VoiceCallRepository
from app.services.voice_call_pagination import (
    VoiceCallCursor,
    VoiceCallEventCursor,
    decode_voice_call_cursor,
    decode_voice_call_event_cursor,
    encode_voice_call_cursor,
    encode_voice_call_event_cursor,
)


class VoiceCallNotFoundError(LookupError):
    """Raised when a voice call cannot be found."""


class InvalidVoiceCallLimitError(ValueError):
    """Raised when a voice call page size is invalid."""


@dataclass(frozen=True, slots=True)
class VoiceCallListFilters:
    status: VoiceCallStatus | None = None
    provider: str | None = None
    provider_call_id: str | None = None
    created_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class VoiceCallListResult:
    items: Sequence[VoiceCall]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class VoiceCallEventListResult:
    items: Sequence[VoiceCallEvent]
    next_cursor: str | None


class VoiceCallInspectionService:
    def __init__(self, *, repository: VoiceCallRepository) -> None:
        self.repository = repository

    def get_voice_call(self, voice_call_id: UUID) -> VoiceCall:
        voice_call = self.repository.get_by_id(voice_call_id)
        if voice_call is None:
            raise VoiceCallNotFoundError("voice call was not found")

        return voice_call

    def list_voice_calls(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        filters: VoiceCallListFilters | None = None,
    ) -> VoiceCallListResult:
        if limit < 1 or limit > 100:
            raise InvalidVoiceCallLimitError("limit must be between 1 and 100")

        decoded_cursor = decode_voice_call_cursor(cursor) if cursor is not None else None
        normalized_filters = filters or VoiceCallListFilters()
        fetched_items = list(
            self.repository.list_voice_calls(
                limit=limit + 1,
                cursor=decoded_cursor,
                status=normalized_filters.status,
                provider=normalized_filters.provider,
                provider_call_id=normalized_filters.provider_call_id,
                created_after=normalized_filters.created_after,
            ),
        )

        items = fetched_items[:limit]
        next_cursor = None
        if len(fetched_items) > limit:
            last_item = items[-1]
            next_cursor = encode_voice_call_cursor(
                VoiceCallCursor(created_at=last_item.created_at, id=last_item.id),
            )

        return VoiceCallListResult(items=items, next_cursor=next_cursor)

    def list_voice_call_events(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
        cursor: str | None = None,
    ) -> VoiceCallEventListResult:
        if limit < 1 or limit > 100:
            raise InvalidVoiceCallLimitError("limit must be between 1 and 100")

        if self.repository.get_by_id(voice_call_id) is None:
            raise VoiceCallNotFoundError("voice call was not found")

        decoded_cursor = (
            decode_voice_call_event_cursor(cursor) if cursor is not None else None
        )
        fetched_items = list(
            self.repository.list_events_for_call(
                voice_call_id=voice_call_id,
                limit=limit + 1,
                cursor=decoded_cursor,
            ),
        )

        items = fetched_items[:limit]
        next_cursor = None
        if len(fetched_items) > limit:
            last_item = items[-1]
            next_cursor = encode_voice_call_event_cursor(
                VoiceCallEventCursor(
                    occurred_at=last_item.occurred_at,
                    id=last_item.id,
                ),
            )

        return VoiceCallEventListResult(items=items, next_cursor=next_cursor)
