from __future__ import annotations

from uuid import UUID

from app.domain.conversations.enums import ConversationChannel
from app.domain.voice_conversation import (
    ConversationNotFoundForBridgeError,
    VoiceCallNotFoundForBridgeError,
    VoiceConversationContext,
    VoiceConversationDebugContext,
    VoiceConversationLinkConflictError,
    extract_scheduling_summaries,
    read_voice_context,
)
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.repositories.conversations import ConversationRepository
from app.repositories.voice_calls import VoiceCallRepository
from app.services.conversations import ConversationCreate, ConversationService


class VoiceConversationBridgeService:
    def __init__(
        self,
        *,
        voice_calls: VoiceCallRepository,
        conversations: ConversationRepository,
        conversation_service: ConversationService | None = None,
    ) -> None:
        self.voice_calls = voice_calls
        self.conversations = conversations
        self.conversation_service = conversation_service or ConversationService(
            repository=conversations,
        )

    def get_or_create_conversation_for_call(
        self,
        provider: str,
        provider_call_id: str,
    ) -> Conversation:
        voice_call = self._get_voice_call_or_raise(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        if voice_call.conversation_id is not None:
            return self._get_conversation_or_raise(voice_call.conversation_id)

        existing_conversation = self.conversations.get_by_external_id(
            channel=ConversationChannel.VOICE,
            external_conversation_id=provider_call_id,
        )
        if existing_conversation is None:
            existing_conversation = self.conversations.get_by_call_id(provider_call_id)
        if existing_conversation is not None:
            return self.link_voice_call_to_conversation(
                voice_call.id,
                existing_conversation.id,
            )

        conversation = self.conversation_service.create_conversation(
            ConversationCreate(
                channel=ConversationChannel.VOICE,
                external_conversation_id=provider_call_id,
                call_id=provider_call_id,
                conversation_metadata={"source": "retell_voice"},
            ),
        )

        return self.link_voice_call_to_conversation(voice_call.id, conversation.id)

    def link_voice_call_to_conversation(
        self,
        voice_call_id: UUID,
        conversation_id: UUID,
    ) -> Conversation:
        voice_call = self.voice_calls.get_by_id(voice_call_id)
        if voice_call is None:
            raise VoiceCallNotFoundForBridgeError("voice call was not found")

        conversation = self.conversations.get_by_id(conversation_id)
        if conversation is None:
            raise ConversationNotFoundForBridgeError("conversation was not found")

        if voice_call.conversation_id == conversation_id:
            return conversation

        if voice_call.conversation_id is not None:
            raise VoiceConversationLinkConflictError(
                "voice call is already linked to a different conversation",
            )

        voice_call.conversation_id = conversation_id
        self.voice_calls.update_voice_call(voice_call)

        return conversation

    def get_context_for_provider_call(
        self,
        provider: str,
        provider_call_id: str,
    ) -> VoiceConversationContext:
        voice_call = self._get_voice_call_or_raise(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        conversation: Conversation | None = None
        if voice_call.conversation_id is not None:
            conversation = self.conversations.get_by_id(voice_call.conversation_id)

        active_hold = None
        scheduling_preference = None
        channel: ConversationChannel | None = None
        conversation_id: UUID | None = None

        if conversation is not None:
            channel = conversation.channel
            conversation_id = conversation.id
            active_hold, scheduling_preference = extract_scheduling_summaries(
                conversation.conversation_metadata,
            )

        return VoiceConversationContext(
            provider_call_id=voice_call.provider_call_id,
            voice_call_id=voice_call.id,
            conversation_id=conversation_id,
            call_status=voice_call.status,
            channel=channel,
            active_hold=active_hold,
            scheduling_preference=scheduling_preference,
        )

    def get_debug_context_for_voice_call(
        self,
        voice_call_id: UUID,
    ) -> VoiceConversationDebugContext:
        voice_call = self.voice_calls.get_by_id(voice_call_id)
        if voice_call is None:
            raise VoiceCallNotFoundForBridgeError("voice call was not found")

        context = self.get_context_for_provider_call(
            voice_call.provider,
            voice_call.provider_call_id,
        )

        voice_context: dict[str, object] = {}
        if voice_call.conversation_id is not None:
            conversation = self.conversations.get_by_id(voice_call.conversation_id)
            if conversation is not None:
                voice_context = read_voice_context(conversation.conversation_metadata)

        scheduling = context.scheduling_preference

        return VoiceConversationDebugContext(
            voice_call_id=voice_call.id,
            provider=voice_call.provider,
            provider_call_id=voice_call.provider_call_id,
            call_status=context.call_status,
            conversation_id=context.conversation_id,
            conversation_channel=context.channel,
            active_hold=context.active_hold,
            requested_specialty=_safe_string_from_voice_context(voice_context, "specialty_name"),
            requested_date=(
                scheduling.requested_date
                if scheduling is not None
                else _safe_string_from_voice_context(voice_context, "requested_date")
            ),
            requested_time_window=(
                scheduling.requested_time_window
                if scheduling is not None
                else _safe_time_window(voice_context.get("requested_time_window"))
            ),
            last_selected_slot_id=(
                scheduling.selected_availability_slot_id
                if scheduling is not None
                else _safe_string_from_voice_context(
                    voice_context,
                    "selected_availability_slot_id",
                )
            ),
        )

    def _get_voice_call_or_raise(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall:
        normalized_provider_call_id = provider_call_id.strip()
        if not normalized_provider_call_id:
            raise VoiceCallNotFoundForBridgeError("voice call was not found")

        voice_call = self.voice_calls.get_by_provider_call_id(
            provider=provider,
            provider_call_id=normalized_provider_call_id,
        )
        if voice_call is None:
            raise VoiceCallNotFoundForBridgeError("voice call was not found")

        return voice_call

    def _get_conversation_or_raise(self, conversation_id: UUID) -> Conversation:
        conversation = self.conversations.get_by_id(conversation_id)
        if conversation is None:
            raise ConversationNotFoundForBridgeError("conversation was not found")

        return conversation


def _safe_string_from_voice_context(
    voice_context: dict[str, object],
    key: str,
) -> str | None:
    value = voice_context.get(key)
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None

    return str(value)


def _safe_time_window(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None

    safe_window: dict[str, str] = {}
    for key in ("label", "start", "end"):
        normalized = _safe_string_from_voice_context(value, key)
        if normalized is not None:
            safe_window[key] = normalized

    return safe_window or None
