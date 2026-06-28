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
        ("Yes", True),
        ("yes please", True),
        ("sure", True),
        ("Yes, book that slot for me", True),
        ("Book it please", True),
        ("Go ahead with the booking", True),
        ("Please confirm my appointment", False),
        ("I would like to book an appointment", False),
        ("Can you check availability?", False),
        ("Maybe", False),
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


def test_parse_seed_demo_phone_format(chat_service: ChatReceptionistService) -> None:
    message = "John Miller, 1985-04-12, +1-555-0201, john.miller@example.test"

    identity = chat_service.parse_patient_identity(message)

    assert identity.full_name == "John Miller"
    assert identity.date_of_birth == "1985-04-12"
    assert identity.phone == "+1-555-0201"
    assert identity.email == "john.miller@example.test"
    assert identity.is_complete()


def test_format_missing_identity_single_field(chat_service: ChatReceptionistService) -> None:
    assert chat_service._format_missing_identity_fields(["phone"]) == "phone"


def test_fresh_identity_only_message_returns_menu_without_storing_identity(
    chat_service: ChatReceptionistService,
) -> None:
    result = chat_service.handle_message(
        ChatMessageInput(message="Jane Doe, 1990-05-15, jane.doe@example.com"),
    )

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "patient_identity" not in chat_context
    assert result.intent == ChatReceptionistIntent.FALLBACK
    reply = result.reply.lower()
    assert "phone" not in reply
    assert "email" not in reply
    assert "book" in reply
    assert "reschedule" in reply
    assert "cancel" in reply


@pytest.mark.parametrize(
    "message",
    [
        "John Smith, 19/09/1996",
        "John Smith, 1996-09-19",
        "John Smith",
    ],
)
def test_fresh_identity_only_opener_returns_menu_prompt(
    chat_service: ChatReceptionistService,
    message: str,
) -> None:
    result = chat_service.handle_message(ChatMessageInput(message=message))

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "patient_identity" not in chat_context
    assert result.intent == ChatReceptionistIntent.FALLBACK
    reply = result.reply.lower()
    assert "phone" not in reply
    assert "email" not in reply
    assert "book" in reply
    assert "what would you like" in reply


def test_scheduling_message_with_identity_routes_to_scheduling_not_menu(
    chat_service: ChatReceptionistService,
) -> None:
    result = chat_service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology for John Smith, 1996-09-19",
        ),
    )

    reply = result.reply.lower()
    assert result.intent != ChatReceptionistIntent.FALLBACK
    assert "what would you like" not in reply
    assert "phone" not in reply or "dermatology" in reply


def test_fresh_email_only_identity_returns_menu_without_storing_identity(
    chat_service: ChatReceptionistService,
) -> None:
    result = chat_service.handle_message(
        ChatMessageInput(message="jane.doe@example.com"),
    )

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "patient_identity" not in chat_context
    assert result.intent == ChatReceptionistIntent.FALLBACK
    assert "phone" not in result.reply.lower()
    assert "email" not in result.reply.lower()


def test_active_hold_identity_intake_continues_for_partial_identity(
    chat_service: ChatReceptionistService,
) -> None:
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

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "seen" in result.reply.lower() or "before" in result.reply.lower()
    assert "what would you like" not in result.reply.lower()


def test_active_hold_full_identity_proceeds_to_booking_identity_flow(
    chat_service: ChatReceptionistService,
) -> None:
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
            message=(
                "My name is Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com"
            ),
            conversation_id=hold.conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "what would you like" not in reply
    assert "seen" in reply or "before" in reply


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

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "seen" in result.reply.lower() or "before" in result.reply.lower()


def test_parse_slash_dob_with_name(chat_service: ChatReceptionistService) -> None:
    identity = chat_service.parse_patient_identity("John Smith, 19/09/1996")

    assert identity.full_name == "John Smith"
    assert identity.date_of_birth == "1996-09-19"


def test_parse_slash_dob_only_in_booking_context(chat_service: ChatReceptionistService) -> None:
    identity = chat_service.parse_patient_identity(
        "19/09/1996",
        booking_context=True,
    )

    assert identity.full_name is None
    assert identity.date_of_birth == "1996-09-19"


def test_parse_slash_dob_without_cue_is_ignored(chat_service: ChatReceptionistService) -> None:
    identity = chat_service.parse_patient_identity("19/09/1996")

    assert identity.date_of_birth is None


def test_parse_ambiguous_slash_dob_preserves_name(chat_service: ChatReceptionistService) -> None:
    identity = chat_service.parse_patient_identity("John Smith, 09/08/1980")

    assert identity.full_name == "John Smith"
    assert identity.date_of_birth is None


@pytest.mark.parametrize(
    "message",
    [
        "John Smith, 1996-09-19",
        "John Smith, September 19, 1996",
    ],
)
def test_existing_dob_formats_still_parse(
    chat_service: ChatReceptionistService,
    message: str,
) -> None:
    identity = chat_service.parse_patient_identity(message)

    assert identity.full_name == "John Smith"
    assert identity.date_of_birth == "1996-09-19"


@pytest.mark.parametrize(
    "message",
    [
        "32/09/1996",
        "19/19/1996",
    ],
)
def test_invalid_slash_dob_does_not_parse(
    chat_service: ChatReceptionistService,
    message: str,
) -> None:
    identity = chat_service.parse_patient_identity(
        message,
        booking_context=True,
    )

    assert identity.date_of_birth is None


def test_should_enter_booking_flow_requires_scheduling_context_for_identity_only(
    chat_service: ChatReceptionistService,
) -> None:
    assert chat_service._should_enter_booking_flow(
        hold_id=None,
        has_identity_fields=True,
        has_confirmation=False,
        offered_slots=[],
        merged_context={},
    ) is False
    # Merely-offered slots are not enough to start identity intake; the user must
    # have committed to a specific slot (selected or held) first.
    assert chat_service._should_enter_booking_flow(
        hold_id=None,
        has_identity_fields=True,
        has_confirmation=False,
        offered_slots=[{"display_time": "09:00"}],
        merged_context={"offered_slots": [{"display_time": "09:00"}]},
    ) is False
    assert chat_service._should_enter_booking_flow(
        hold_id=None,
        has_identity_fields=True,
        has_confirmation=False,
        offered_slots=[{"display_time": "09:00"}],
        merged_context={"selected_availability_slot_id": "slot-1"},
    ) is True
    assert chat_service._should_enter_booking_flow(
        hold_id="hold-1",
        has_identity_fields=True,
        has_confirmation=False,
        offered_slots=[],
        merged_context={"hold_id": "hold-1"},
    ) is True
