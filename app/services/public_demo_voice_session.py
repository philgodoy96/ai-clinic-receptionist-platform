from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.retell_web_call import RetellWebCallRequest, RetellWebCallResult
from app.repositories.voice_calls import VoiceCallRepository
from app.services.conversations import ConversationNotFoundError, ConversationService
from app.services.retell_web_call import (
    RETELL_WEB_CALL_SOURCE,
    RetellWebCallService,
)
from app.services.voice_conversation_bridge import VoiceConversationBridgeService


class PublicDemoVoiceSessionConversationNotFoundError(Exception):
    """Raised when a requested conversation does not exist."""


@dataclass(frozen=True, slots=True)
class PublicDemoVoiceSessionRequest:
    demo_session_id: str | None = None
    conversation_id: UUID | None = None


class PublicDemoVoiceSessionService:
    def __init__(
        self,
        *,
        retell_web_call_service: RetellWebCallService,
        voice_calls: VoiceCallRepository,
        voice_conversation_bridge: VoiceConversationBridgeService,
        conversations: ConversationService,
    ) -> None:
        self._retell_web_call_service = retell_web_call_service
        self._voice_calls = voice_calls
        self._voice_conversation_bridge = voice_conversation_bridge
        self._conversations = conversations

    def create_session(self, request: PublicDemoVoiceSessionRequest) -> RetellWebCallResult:
        if request.conversation_id is not None:
            self._ensure_conversation_exists(request.conversation_id)

        web_call_result = self._retell_web_call_service.create_web_call(
            RetellWebCallRequest(
                demo_session_id=request.demo_session_id,
                conversation_id=request.conversation_id,
            ),
        )

        voice_call, created = self._voice_calls.get_or_create_voice_call(
            provider=web_call_result.provider,
            provider_call_id=web_call_result.call_id,
        )
        if created:
            voice_call.event_metadata = {
                "call_type": "web_call",
                "source": RETELL_WEB_CALL_SOURCE,
            }
            self._voice_calls.update_voice_call(voice_call)

        if request.conversation_id is not None:
            conversation = self._voice_conversation_bridge.link_voice_call_to_conversation(
                voice_call.id,
                request.conversation_id,
            )
            resolved_conversation_id = conversation.id
        else:
            conversation = self._voice_conversation_bridge.get_or_create_conversation_for_call(
                web_call_result.provider,
                web_call_result.call_id,
            )
            resolved_conversation_id = conversation.id

        return RetellWebCallResult(
            provider=web_call_result.provider,
            call_id=web_call_result.call_id,
            access_token=web_call_result.access_token,
            expires_in_seconds=web_call_result.expires_in_seconds,
            conversation_id=resolved_conversation_id,
        )

    def _ensure_conversation_exists(self, conversation_id: UUID) -> None:
        try:
            self._conversations.get_conversation(conversation_id)
        except ConversationNotFoundError as exc:
            raise PublicDemoVoiceSessionConversationNotFoundError(
                "conversation was not found",
            ) from exc
