from __future__ import annotations

import pytest

from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatPatientIdentity,
    ChatReceptionistIntent,
    ChatReceptionistService,
    merge_patient_identity,
    message_has_confirmation,
)
from app.services.conversations import ConversationService
from tests.test_chat_receptionist_service import (
    _create_hold_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


@pytest.fixture()
def chat_service() -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=_create_hold_service(),
    )


def test_parse_full_structured_sentence(chat_service: ChatReceptionistService) -> None:
    message = (
        "My name is Jane Doe, date of birth 1990-05-15, "
        "phone +1 555-123-4567, email jane.doe@example.com"
    )

    identity = chat_service.parse_patient_identity(message)

    assert identity.full_name == "Jane Doe"
    assert identity.date_of_birth == "1990-05-15"
    assert identity.phone == "+1 555-123-4567"
    assert identity.email == "jane.doe@example.com"
    assert identity.is_complete()


def test_parse_comma_separated_format(chat_service: ChatReceptionistService) -> None:
    message = "John Smith, 1985-11-20, 555-987-6543, john.smith@clinic.test"

    identity = chat_service.parse_patient_identity(message)

    assert identity.full_name == "John Smith"
    assert identity.date_of_birth == "1985-11-20"
    assert identity.phone == "555-987-6543"
    assert identity.email == "john.smith@clinic.test"
    assert identity.is_complete()


def test_missing_fields_detection() -> None:
    identity = ChatPatientIdentity(
        full_name="Jane Doe",
        date_of_birth="1990-05-15",
    )

    assert identity.missing_fields() == ["phone", "email"]
    assert not identity.is_complete()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Please confirm my appointment", True),
        ("Yes, book that slot for me", True),
        ("Book it please", True),
        ("Schedule it for tomorrow", True),
        ("Go ahead with the booking", True),
        ("I would like to book an appointment", False),
        ("Can you check availability?", False),
    ],
)
def test_message_has_confirmation(message: str, expected: bool) -> None:
    assert message_has_confirmation(message) is expected


def test_merge_patient_identity_preserves_existing_values() -> None:
    existing = {
        "full_name": "Jane Doe",
        "date_of_birth": "1990-05-15",
        "phone": "+1 555-000-1111",
    }
    parsed = ChatPatientIdentity(
        full_name="John Smith",
        date_of_birth="1985-01-01",
        phone="+1 555-999-8888",
        email="jane.doe@example.com",
    )

    merged = merge_patient_identity(existing, parsed)

    assert merged == {
        "full_name": "Jane Doe",
        "date_of_birth": "1990-05-15",
        "phone": "+1 555-000-1111",
        "email": "jane.doe@example.com",
    }


def test_partial_identity_stored_in_chat_context(chat_service: ChatReceptionistService) -> None:
    result = chat_service.handle_message(
        ChatMessageInput(message="Jane Doe, 1990-05-15, jane.doe@example.com"),
    )

    patient_identity = result.conversation.conversation_metadata["chat_context"][
        "patient_identity"
    ]

    assert patient_identity["full_name"] == "Jane Doe"
    assert patient_identity["date_of_birth"] == "1990-05-15"
    assert patient_identity["email"] == "jane.doe@example.com"
    assert "phone" not in patient_identity
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "phone" in result.reply.lower()


def test_partial_identity_with_active_hold(chat_service: ChatReceptionistService) -> None:
    availability = chat_service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = chat_service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = chat_service.handle_message(
        ChatMessageInput(
            message="My name is Jane Doe, 1990-05-15, jane.doe@example.com",
            conversation_id=hold.conversation.id,
        ),
    )

    patient_identity = result.conversation.conversation_metadata["chat_context"][
        "patient_identity"
    ]

    assert patient_identity["full_name"] == "Jane Doe"
    assert patient_identity["date_of_birth"] == "1990-05-15"
    assert patient_identity["email"] == "jane.doe@example.com"
    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert "hold is still active" in result.reply.lower()
    assert hold.conversation.conversation_metadata["chat_context"]["hold_id"] == (
        result.conversation.conversation_metadata["chat_context"]["hold_id"]
    )
