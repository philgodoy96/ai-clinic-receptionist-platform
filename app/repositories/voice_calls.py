from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.services.voice_call_pagination import VoiceCallCursor, VoiceCallEventCursor


class VoiceCallRepository(Protocol):
    """Persistence boundary for voice calls used by lifecycle and bridge services."""

    def get_by_id(self, voice_call_id: UUID) -> VoiceCall | None:
        raise NotImplementedError

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        raise NotImplementedError

    def create_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        raise NotImplementedError

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        raise NotImplementedError

    def update_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        raise NotImplementedError

    def create_voice_call_event(self, voice_call_event: VoiceCallEvent) -> VoiceCallEvent:
        raise NotImplementedError

    def get_event_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> VoiceCallEvent | None:
        raise NotImplementedError

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

    def list_events_for_call(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
        cursor: VoiceCallEventCursor | None = None,
    ) -> Sequence[VoiceCallEvent]:
        raise NotImplementedError
