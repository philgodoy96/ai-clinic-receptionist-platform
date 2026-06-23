from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.domain.appointments import (
    AppointmentCancellationRequest,
    AppointmentCancellationResult,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.conversations import ConversationService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_service import create_booking_context
from tests.test_appointment_cancellation_service import create_cancellation_context
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-cancel-1"
TOOL_CALL_ID = "tool-call-cancel-1"


class TrackingAppointmentCancellationService:
    def __init__(self, inner: AppointmentCancellationService) -> None:
        self.inner = inner
        self.cancel_calls: list[AppointmentCancellationRequest] = []

    def cancel_appointment(
        self,
        request: AppointmentCancellationRequest,
    ) -> AppointmentCancellationResult:
        self.cancel_calls.append(request)
        return self.inner.cancel_appointment(request)


def cancellation_arguments(
    *,
    appointment_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": "Yes, please cancel it.",
        "cancellation_reason": "Patient requested cancellation",
    }
    if appointment_id is not None:
        payload["appointment_id"] = appointment_id
    return payload


def cancellation_tool_request(
    *,
    appointment_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "cancel_appointment",
            "arguments": cancellation_arguments(
                appointment_id=appointment_id,
                explicit_confirmation=explicit_confirmation,
                tool_call_id=tool_call_id,
            ),
        },
    )


def create_retell_cancellation_tool_context(
    *,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> dict[str, Any]:
    cancellation_context = create_cancellation_context(status=status)
    appointment = cancellation_context.appointment
    booking_context = create_booking_context()

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        appointment_id=appointment.id,
        conversation_metadata={
            "voice_context": {
                "appointment_id": str(appointment.id),
            },
        },
    )
    conversation_repository.conversations.append(conversation)

    voice_calls = FakeVoiceCallRepository()
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=PROVIDER_CALL_ID,
        status=VoiceCallStatus.IN_PROGRESS,
        conversation_id=conversation.id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)

    conversations = ConversationService(repository=conversation_repository)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=conversations,
    )

    tracking_cancellation = TrackingAppointmentCancellationService(
        cancellation_context.service,
    )
    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=booking_context.booking_service.doctors,
        patients=booking_context.booking_service.patients,
        availability_slots=booking_context.booking_service.availability_slots,
        appointments=cancellation_context.appointment_repository,
    )

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=booking_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        appointment_cancellation=tracking_cancellation,
        appointments=cancellation_context.appointment_repository,
        clinic_time_service=make_test_clinic_time_service(),
    )

    return {
        "adapter": adapter,
        "tracking_cancellation": tracking_cancellation,
        "cancellation_context": cancellation_context,
        "conversation": conversation,
        "conversation_repository": conversation_repository,
        "appointment": appointment,
    }
