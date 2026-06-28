from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from app.core.config import Settings
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistIntent
from app.services.conversations import ConversationService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)
from tests.test_settings import load_settings


def test_settings_default_appointment_hold_ttls() -> None:
    settings = Settings(_env_file=None)

    assert settings.appointment_hold_ttl_seconds == 300
    assert settings.chat_appointment_hold_ttl_seconds == 600


def test_settings_chat_hold_ttl_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        APPOINTMENT_HOLD_TTL_SECONDS="300",
        CHAT_APPOINTMENT_HOLD_TTL_SECONDS="900",
    )

    assert settings.appointment_hold_ttl_seconds == 300
    assert settings.chat_appointment_hold_ttl_seconds == 900


def test_chat_booking_hold_creation_uses_chat_ttl() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    hold_service = FakeAppointmentHoldService(ttl_seconds=300)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        chat_appointment_hold_ttl_seconds=600,
    )

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert len(hold_service.create_hold_calls) == 1
    assert hold_service.create_hold_calls[0]["ttl_seconds"] == 600

    chat_context = result.conversation.conversation_metadata["chat_context"]
    hold_expires_at = datetime.fromisoformat(chat_context["hold_expires_at"])
    hold = hold_service.repository.get_by_hold_id(UUID(chat_context["hold_id"]))
    assert hold is not None
    assert hold_expires_at == hold.created_at + timedelta(seconds=600)
