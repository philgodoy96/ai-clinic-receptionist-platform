from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.chat_receptionist import ChatReceptionistIntent


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    patient_id: UUID | None = None
    conversation_metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessageResponse(BaseModel):
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    intent: ChatReceptionistIntent
    reply: str
    appointment_id: UUID | None = None
    booking_confirmed: bool = False
    confirmation_email_queued: bool | None = None
