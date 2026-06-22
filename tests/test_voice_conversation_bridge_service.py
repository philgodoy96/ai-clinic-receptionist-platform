from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_conversation import (
    VoiceCallNotFoundForBridgeError,
    VoiceConversationLinkConflictError,
)
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.services.conversations import ConversationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository


@pytest.fixture()
def bridge_bundle() -> tuple[
    VoiceConversationBridgeService,
    FakeVoiceCallRepository,
    FakeConversationRepository,
]:
    voice_calls = FakeVoiceCallRepository()
    conversations = FakeConversationRepository()
    service = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversations,
        conversation_service=ConversationService(repository=conversations),
    )

    return service, voice_calls, conversations


def _create_voice_call(
    voice_calls: FakeVoiceCallRepository,
    *,
    provider_call_id: str = "call-123",
    conversation_id: UUID | None = None,
) -> VoiceCall:
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=provider_call_id,
        status=VoiceCallStatus.IN_PROGRESS,
        conversation_id=conversation_id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)
    return voice_call


def test_creates_voice_conversation_for_unlinked_voice_call(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations = bridge_bundle
    voice_call = _create_voice_call(voice_calls)

    conversation = service.get_or_create_conversation_for_call("retell", "call-123")

    assert conversation.channel == ConversationChannel.VOICE
    assert conversation.status == ConversationStatus.ACTIVE
    assert conversation.external_conversation_id == "call-123"
    assert conversation.call_id == "call-123"
    assert len(conversations.conversations) == 1
    assert voice_call.conversation_id == conversation.id


def test_returns_existing_conversation_for_already_linked_voice_call(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    existing = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id="call-linked",
        call_id="call-linked",
    )
    conversations.conversations.append(existing)
    _create_voice_call(
        voice_calls,
        provider_call_id="call-linked",
        conversation_id=existing.id,
    )

    conversation = service.get_or_create_conversation_for_call("retell", "call-linked")

    assert conversation.id == existing.id
    assert len(conversations.conversations) == 1


def test_duplicate_bridge_calls_are_idempotent(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    _create_voice_call(voice_calls)

    first = service.get_or_create_conversation_for_call("retell", "call-123")
    second = service.get_or_create_conversation_for_call("retell", "call-123")

    assert second.id == first.id
    assert len(conversations.conversations) == 1
    assert voice_calls.voice_calls[0].conversation_id == first.id


def test_missing_voice_call_handled_safely(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, *_ = bridge_bundle

    with pytest.raises(VoiceCallNotFoundForBridgeError):
        service.get_or_create_conversation_for_call("retell", "missing-call")

    with pytest.raises(VoiceCallNotFoundForBridgeError):
        service.get_context_for_provider_call("retell", "missing-call")


def test_conflict_detected_when_relinking_voice_call(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    linked_conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
    )
    other_conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
    )
    conversations.conversations.extend([linked_conversation, other_conversation])
    voice_call = _create_voice_call(
        voice_calls,
        conversation_id=linked_conversation.id,
    )

    with pytest.raises(VoiceConversationLinkConflictError):
        service.link_voice_call_to_conversation(voice_call.id, other_conversation.id)


def test_context_response_is_safe(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-1",
                "availability_slot_id": str(uuid4()),
                "doctor_id": str(uuid4()),
                "start_time": "2026-07-01T09:00:00+00:00",
                "end_time": "2026-07-01T09:30:00+00:00",
                "requested_date": "2026-07-01",
                "requested_time_window": {
                    "label": "morning",
                    "start": "09:00",
                    "end": "12:00",
                },
                "selected_availability_slot_id": str(uuid4()),
                "transcript": "secret symptoms",
                "raw_payload": {"nested": "value"},
                "api_key": "secret-key",
                "phone_number": "+15551234567",
            },
        },
    )
    conversations.conversations.append(conversation)
    _create_voice_call(
        voice_calls,
        provider_call_id="call-safe",
        conversation_id=conversation.id,
    )

    context = service.get_context_for_provider_call("retell", "call-safe")
    serialized = repr(context)

    assert context.provider_call_id == "call-safe"
    assert context.conversation_id == conversation.id
    assert context.channel == ConversationChannel.VOICE
    assert context.call_status == VoiceCallStatus.IN_PROGRESS
    assert context.active_hold is not None
    assert context.active_hold.hold_id == "hold-1"
    assert context.scheduling_preference is not None
    assert context.scheduling_preference.requested_date == "2026-07-01"
    assert context.scheduling_preference.requested_time_window == {
        "label": "morning",
        "start": "09:00",
        "end": "12:00",
    }
    assert "secret symptoms" not in serialized
    assert "secret-key" not in serialized
    assert "+15551234567" not in serialized
    assert "raw_payload" not in serialized


def test_no_booking_email_or_llm_service_is_called(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations = bridge_bundle
    _create_voice_call(voice_calls)

    service.get_or_create_conversation_for_call("retell", "call-123")
    service.get_context_for_provider_call("retell", "call-123")

    assert not hasattr(service, "booking_service")
    assert not hasattr(service, "email_jobs")
    assert not hasattr(service, "llm_service")
    assert len(conversations.conversations) == 1
    assert voice_calls.voice_calls[0].conversation_id == conversations.conversations[0].id


def test_get_debug_context_for_linked_voice_call(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    slot_id = str(uuid4())
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-debug",
                "availability_slot_id": slot_id,
                "specialty_name": "Cardiology",
                "requested_date": "2026-07-05",
                "selected_availability_slot_id": slot_id,
            },
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        provider_call_id="call-debug",
        conversation_id=conversation.id,
    )

    debug_context = service.get_debug_context_for_voice_call(voice_call.id)

    assert debug_context.voice_call_id == voice_call.id
    assert debug_context.provider == "retell"
    assert debug_context.provider_call_id == "call-debug"
    assert debug_context.conversation_id == conversation.id
    assert debug_context.conversation_channel == ConversationChannel.VOICE
    assert debug_context.active_hold is not None
    assert debug_context.active_hold.hold_id == "hold-debug"
    assert debug_context.requested_specialty == "Cardiology"
    assert debug_context.requested_date == "2026-07-05"
    assert debug_context.last_selected_slot_id == slot_id


def test_get_debug_context_missing_voice_call_raises(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, *_ = bridge_bundle

    with pytest.raises(VoiceCallNotFoundForBridgeError):
        service.get_debug_context_for_voice_call(uuid4())


def test_get_debug_context_excludes_raw_provider_payload_and_secrets(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations, *_ = bridge_bundle
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-safe-debug",
                "requested_date": "2026-07-06",
                "transcript": "secret symptoms",
                "raw_payload": {"nested": "value"},
                "api_key": "secret-key",
                "phone_number": "+15551234567",
            },
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        provider_call_id="call-safe-debug",
        conversation_id=conversation.id,
    )

    debug_context = service.get_debug_context_for_voice_call(voice_call.id)
    serialized = repr(debug_context)

    assert debug_context.active_hold is not None
    assert debug_context.requested_date == "2026-07-06"
    assert "secret symptoms" not in serialized
    assert "secret-key" not in serialized
    assert "+15551234567" not in serialized
    assert "raw_payload" not in serialized
