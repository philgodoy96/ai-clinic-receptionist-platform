from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_appointment_hold_service,
    get_chat_receptionist_service,
)
from app.api.errors import APIError
from app.db.session import get_db
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.services.appointment_holds import AppointmentHoldService
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistService
from app.services.conversations import (
    ConversationNotFoundError,
    InvalidConversationMessageError,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post(
    "/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_200_OK,
)
def send_chat_message(
    payload: ChatMessageRequest,
    service: Annotated[ChatReceptionistService, Depends(get_chat_receptionist_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessageResponse:
    try:
        result = service.handle_message(
            ChatMessageInput(
                message=payload.message,
                conversation_id=payload.conversation_id,
                patient_id=payload.patient_id,
                conversation_metadata=payload.conversation_metadata,
            ),
        )
        db.commit()

        if result.pending_hold_release is not None:
            hold_service.release_hold(
                doctor_id=result.pending_hold_release.doctor_id,
                start_time=result.pending_hold_release.start_time,
                owner_id=result.pending_hold_release.owner_id,
            )
    except ConversationNotFoundError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message="Conversation was not found.",
        ) from exc
    except InvalidConversationMessageError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_conversation_message",
            message="Conversation message is invalid.",
        ) from exc
    except Exception:
        db.rollback()
        raise

    return ChatMessageResponse(
        conversation_id=result.conversation.id,
        user_message_id=result.user_message.id,
        assistant_message_id=result.assistant_message.id,
        intent=result.intent,
        reply=result.reply,
    )