from __future__ import annotations

from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_voice_conversation_bridge_service
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.main import create_app
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.schemas.voice_conversation import voice_conversation_context_response_field_names
from app.services.conversations import ConversationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository

BASE_PATH = "/api/v1/internal/voice-calls"


@pytest.fixture()
def client_and_bridge() -> Generator[
    tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
    None,
    None,
]:
    voice_calls = FakeVoiceCallRepository()
    conversations = FakeConversationRepository()
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversations,
        conversation_service=ConversationService(repository=conversations),
    )
    app = create_app()
    app.dependency_overrides[get_voice_conversation_bridge_service] = lambda: bridge

    with TestClient(app) as test_client:
        yield test_client, voice_calls, conversations, bridge

    app.dependency_overrides.clear()


def _create_voice_call(
    voice_calls: FakeVoiceCallRepository,
    *,
    provider_call_id: str = "call-debug",
    conversation_id: UUID | None = None,
    status: VoiceCallStatus = VoiceCallStatus.IN_PROGRESS,
) -> VoiceCall:
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=provider_call_id,
        status=status,
        conversation_id=conversation_id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)
    return voice_call


def test_returns_conversation_context_for_linked_voice_call(
    client_and_bridge: tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
) -> None:
    client, voice_calls, conversations, _bridge = client_and_bridge
    slot_id = str(uuid4())
    doctor_id = str(uuid4())
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id="call-debug",
        call_id="call-debug",
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-1",
                "availability_slot_id": slot_id,
                "doctor_id": doctor_id,
                "start_time": "2026-07-01T09:00:00+00:00",
                "end_time": "2026-07-01T09:30:00+00:00",
                "specialty_name": "Cardiology",
                "requested_date": "2026-07-01",
                "requested_time_window": {
                    "label": "morning",
                    "start": "09:00",
                    "end": "12:00",
                },
                "selected_availability_slot_id": slot_id,
            },
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        conversation_id=conversation.id,
    )

    response = client.get(f"{BASE_PATH}/{voice_call.id}/conversation-context")

    assert response.status_code == 200
    body = response.json()
    assert body["voice_call_id"] == str(voice_call.id)
    assert body["provider"] == "retell"
    assert body["provider_call_id"] == "call-debug"
    assert body["call_status"] == VoiceCallStatus.IN_PROGRESS.value
    assert body["conversation_id"] == str(conversation.id)
    assert body["conversation_channel"] == ConversationChannel.VOICE.value
    assert body["active_hold_summary"] == {
        "hold_id": "hold-1",
        "availability_slot_id": slot_id,
        "doctor_id": doctor_id,
        "start_time": "2026-07-01T09:00:00+00:00",
        "end_time": "2026-07-01T09:30:00+00:00",
    }
    assert body["requested_specialty"] == "Cardiology"
    assert body["requested_date"] == "2026-07-01"
    assert body["requested_time_window"] == {
        "label": "morning",
        "start": "09:00",
        "end": "12:00",
    }
    assert body["last_selected_slot_id"] == slot_id


def test_missing_voice_call_returns_404(
    client_and_bridge: tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
) -> None:
    client, *_ = client_and_bridge
    missing_id = uuid4()

    response = client.get(f"{BASE_PATH}/{missing_id}/conversation-context")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_call_not_found"


def test_returns_safe_empty_context_when_no_active_hold(
    client_and_bridge: tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
) -> None:
    client, voice_calls, conversations, _bridge = client_and_bridge
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "requested_date": "2026-07-02",
                "requested_time_window": {
                    "label": "afternoon",
                    "start": "13:00",
                    "end": "17:00",
                },
            },
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        provider_call_id="call-no-hold",
        conversation_id=conversation.id,
    )

    response = client.get(f"{BASE_PATH}/{voice_call.id}/conversation-context")

    assert response.status_code == 200
    body = response.json()
    assert body["active_hold_summary"] is None
    assert body["requested_date"] == "2026-07-02"
    assert body["requested_time_window"] == {
        "label": "afternoon",
        "start": "13:00",
        "end": "17:00",
    }
    assert body["last_selected_slot_id"] is None


def test_response_does_not_expose_raw_metadata_or_secrets(
    client_and_bridge: tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
) -> None:
    client, voice_calls, conversations, _bridge = client_and_bridge
    secret_transcript = "patient said secret symptoms"
    secret_number = "+15551234567"
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-safe",
                "availability_slot_id": str(uuid4()),
                "requested_date": "2026-07-03",
                "transcript": secret_transcript,
                "raw_payload": {"nested": "value"},
                "api_key": "secret-key",
                "phone_number": secret_number,
                "patient_name": "Jane Doe",
            },
            "chat_context": {"email": "patient@example.com"},
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        provider_call_id="call-safe",
        conversation_id=conversation.id,
        status=VoiceCallStatus.ENDED,
    )

    response = client.get(f"{BASE_PATH}/{voice_call.id}/conversation-context")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == voice_conversation_context_response_field_names()
    serialized = str(body)
    assert secret_transcript not in serialized
    assert secret_number not in serialized
    assert "secret-key" not in serialized
    assert "raw_payload" not in serialized
    assert "transcript" not in serialized
    assert "patient@example.com" not in serialized
    assert "Jane Doe" not in serialized
    assert body["active_hold_summary"] is not None
    assert body["active_hold_summary"]["hold_id"] == "hold-safe"


def _snapshot_voice_calls(voice_calls: list[VoiceCall]) -> list[dict[str, object]]:
    return [
        {
            "id": voice_call.id,
            "provider": voice_call.provider,
            "provider_call_id": voice_call.provider_call_id,
            "status": voice_call.status,
            "conversation_id": voice_call.conversation_id,
        }
        for voice_call in voice_calls
    ]


def _snapshot_conversations(conversations: list[Conversation]) -> list[dict[str, object]]:
    return [
        {
            "id": conversation.id,
            "channel": conversation.channel,
            "status": conversation.status,
            "conversation_metadata": deepcopy(conversation.conversation_metadata),
        }
        for conversation in conversations
    ]


def test_get_conversation_context_has_no_side_effects(
    client_and_bridge: tuple[
        TestClient,
        FakeVoiceCallRepository,
        FakeConversationRepository,
        VoiceConversationBridgeService,
    ],
) -> None:
    client, voice_calls, conversations, _bridge = client_and_bridge
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "hold_id": "hold-stable",
                "requested_date": "2026-07-04",
            },
        },
    )
    conversations.conversations.append(conversation)
    voice_call = _create_voice_call(
        voice_calls,
        provider_call_id="call-stable",
        conversation_id=conversation.id,
    )
    voice_calls_before = _snapshot_voice_calls(voice_calls.voice_calls)
    conversations_before = _snapshot_conversations(conversations.conversations)

    response = client.get(f"{BASE_PATH}/{voice_call.id}/conversation-context")

    assert response.status_code == 200
    assert _snapshot_voice_calls(voice_calls.voice_calls) == voice_calls_before
    assert _snapshot_conversations(conversations.conversations) == conversations_before
