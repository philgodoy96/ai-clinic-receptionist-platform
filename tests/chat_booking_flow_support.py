from __future__ import annotations

from typing import cast
from uuid import UUID

from httpx import Response
from starlette.testclient import TestClient

from app.models.conversations import Conversation
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistService,
)

NEW_PATIENT_IDENTITY_STEPS = (
    "No.",
    "Jane Doe.",
    "1990-05-15",
    "jane.doe@example.com",
    "Yes",
)
FINAL_BOOKING_CONFIRM = "Yes"

EXISTING_PATIENT_IDENTITY_STEPS = (
    "Yes.",
    "John Miller, 1985-04-12",
    "john.miller@example.test",
    "Yes, that email is correct.",
)


def send_chat_messages(
    service: ChatReceptionistService,
    conversation_id: UUID,
    messages: tuple[str, ...] | list[str],
) -> ChatMessageResult:
    result: ChatMessageResult | None = None
    for message in messages:
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation_id),
        )
    assert result is not None
    return result


def conversation_with_active_hold(
    service: ChatReceptionistService,
    *,
    availability_message: str = "Dr. Emily Carter on 2026-07-02",
    slot_message: str = "I'll take 09:00",
) -> Conversation:
    availability = service.handle_message(ChatMessageInput(message=availability_message))
    hold = service.handle_message(
        ChatMessageInput(
            message=slot_message,
            conversation_id=availability.conversation.id,
        ),
    )
    return hold.conversation


def advance_new_patient_to_booking_summary(
    service: ChatReceptionistService,
    conversation: Conversation,
) -> ChatMessageResult:
    return send_chat_messages(service, conversation.id, NEW_PATIENT_IDENTITY_STEPS)


def complete_new_patient_booking(
    service: ChatReceptionistService,
    conversation: Conversation,
    *,
    final_confirm: str = FINAL_BOOKING_CONFIRM,
) -> ChatMessageResult:
    advance_new_patient_to_booking_summary(service, conversation)
    return service.handle_message(
        ChatMessageInput(message=final_confirm, conversation_id=conversation.id),
    )


def post_new_patient_booking_via_api(
    client: TestClient,
    conversation_id: str,
) -> Response:
    for message in NEW_PATIENT_IDENTITY_STEPS:
        client.post(
            "/api/v1/chat/messages",
            json={"message": message, "conversation_id": conversation_id},
        )
    return cast(
        Response,
        client.post(
            "/api/v1/chat/messages",
            json={"message": FINAL_BOOKING_CONFIRM, "conversation_id": conversation_id},
        ),
    )


def complete_existing_patient_booking(
    service: ChatReceptionistService,
    conversation: Conversation,
    *,
    final_confirm: str = FINAL_BOOKING_CONFIRM,
) -> ChatMessageResult:
    send_chat_messages(service, conversation.id, EXISTING_PATIENT_IDENTITY_STEPS)
    return service.handle_message(
        ChatMessageInput(message=final_confirm, conversation_id=conversation.id),
    )
