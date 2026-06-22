import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.demo_guardrail_enforcement import enforce_chat_message_allowed
from app.api.dependencies import (
    get_appointment_hold_service,
    get_chat_receptionist_service,
    get_demo_guardrail_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
)
from app.api.errors import APIError
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisher,
    EmailJobDispatchPublisherError,
)
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.services.appointment_holds import AppointmentHoldService
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistService
from app.services.conversations import (
    ConversationNotFoundError,
    InvalidConversationMessageError,
)
from app.services.demo_guardrails import (
    DemoGuardrailLimitExceeded,
    DemoGuardrailService,
    DemoGuardrailStoreUnavailable,
)
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

CHAT_BOOKING_SOURCE = "chat_booking"
logger = logging.getLogger("app.chat")


@router.post(
    "/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_200_OK,
)
def send_chat_message(
    request: Request,
    payload: ChatMessageRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    guardrails: Annotated[DemoGuardrailService, Depends(get_demo_guardrail_service)],
    service: Annotated[ChatReceptionistService, Depends(get_chat_receptionist_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessageResponse:
    client_ip = enforce_chat_message_allowed(request, settings, guardrails)
    confirmation_email_job_id = None
    confirmation_email_queued: bool | None = None
    handoff_notification_email_job_id = None

    try:
        result = service.handle_message(
            ChatMessageInput(
                message=payload.message,
                conversation_id=payload.conversation_id,
                patient_id=payload.patient_id,
                conversation_metadata=payload.conversation_metadata,
            ),
        )

        if (
            result.booking_confirmed
            and result.appointment_id is not None
            and result.booked_patient_id is not None
            and result.booked_appointment_start_time is not None
        ):
            guardrails.record_appointment_created(client_ip)
            confirmation_email_job_id, confirmation_email_queued = (
                _enqueue_confirmation_email_if_allowed(
                    guardrails=guardrails,
                    client_ip=client_ip,
                    email_jobs=email_jobs,
                    appointment_id=result.appointment_id,
                    patient_id=result.booked_patient_id,
                    appointment_start_time=result.booked_appointment_start_time.isoformat(),
                    conversation_id=result.conversation.id,
                    hold_id=result.hold_id_to_release,
                )
            )

        handoff_notification_email_job_id = (
            result.human_handoff_notification_email_job_id
        )

        db.commit()

        if result.hold_id_to_release and result.pending_hold_release is not None:
            try:
                hold_service.release_hold(
                    doctor_id=result.pending_hold_release.doctor_id,
                    start_time=result.pending_hold_release.start_time,
                    owner_id=result.pending_hold_release.owner_id,
                )
            except Exception:
                logger.warning(
                    "chat_hold_release_failed",
                    extra={
                        "event": "chat_hold_release_failed",
                        "source": CHAT_BOOKING_SOURCE,
                        "hold_id": result.hold_id_to_release,
                        "appointment_id": (
                            str(result.appointment_id)
                            if result.appointment_id is not None
                            else None
                        ),
                    },
                )

        if confirmation_email_job_id is not None:
            try:
                email_job_dispatch.publish_email_job_ready(
                    email_job_id=confirmation_email_job_id,
                )
            except EmailJobDispatchPublisherError:
                logger.warning(
                    "email_job_dispatch_publish_failed",
                    extra={
                        "event": "email_job_dispatch_publish_failed",
                        "source": CHAT_BOOKING_SOURCE,
                        "email_job_id": str(confirmation_email_job_id),
                        "appointment_id": (
                            str(result.appointment_id)
                            if result.appointment_id is not None
                            else None
                        ),
                    },
                )

        if handoff_notification_email_job_id is not None:
            try:
                email_job_dispatch.publish_email_job_ready(
                    email_job_id=handoff_notification_email_job_id,
                )
            except EmailJobDispatchPublisherError:
                logger.warning(
                    "human_handoff_notification_dispatch_publish_failed",
                    extra={
                        "event": "human_handoff_notification_dispatch_publish_failed",
                        "email_job_id": str(handoff_notification_email_job_id),
                        "conversation_id": str(result.conversation.id),
                    },
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
        appointment_id=result.appointment_id,
        booking_confirmed=result.booking_confirmed,
        confirmation_email_queued=confirmation_email_queued,
    )


def _enqueue_confirmation_email_if_allowed(
    *,
    guardrails: DemoGuardrailService,
    client_ip: str,
    email_jobs: EmailJobService,
    appointment_id: UUID,
    patient_id: UUID,
    appointment_start_time: str,
    conversation_id: UUID,
    hold_id: str | None,
) -> tuple[UUID | None, bool]:
    try:
        guardrails.check_confirmation_email_allowed(client_ip)
    except DemoGuardrailLimitExceeded as exc:
        logger.warning(
            "demo_confirmation_email_skipped",
            extra={
                "event": "demo_confirmation_email_skipped",
                "reason": "demo_quota_exceeded",
                "limit_name": exc.limit_name,
                "source": CHAT_BOOKING_SOURCE,
                "appointment_id": str(appointment_id),
            },
        )
        return None, False
    except DemoGuardrailStoreUnavailable:
        logger.error(
            "demo_confirmation_email_skipped",
            extra={
                "event": "demo_confirmation_email_skipped",
                "reason": "demo_guardrail_store_unavailable",
                "source": CHAT_BOOKING_SOURCE,
                "appointment_id": str(appointment_id),
            },
        )
        return None, False

    email_job = email_jobs.enqueue_appointment_confirmation(
        AppointmentConfirmationEmailJobCreate(
            appointment_id=appointment_id,
            patient_id=patient_id,
            appointment_start_time=appointment_start_time,
            payload={
                "source": CHAT_BOOKING_SOURCE,
                "hold_id": hold_id,
                "conversation_id": str(conversation_id),
            },
        ),
    )
    guardrails.record_confirmation_email_created(client_ip)
    return email_job.id, True
