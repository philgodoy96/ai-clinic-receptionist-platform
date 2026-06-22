from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent


class VoiceCallRepository(Protocol):
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

    def list_voice_calls(
        self,
        *,
        limit: int,
        status: VoiceCallStatus | None = None,
        provider: str | None = None,
    ) -> Sequence[VoiceCall]:
        raise NotImplementedError

    def list_events_for_call(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
    ) -> Sequence[VoiceCallEvent]:
        raise NotImplementedError
