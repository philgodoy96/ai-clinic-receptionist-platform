from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingRequest,
    AppointmentReschedulingResult,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.test_appointment_booking_service import FakePatientRepository
from tests.test_appointment_rescheduling_service import create_rescheduling_context
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-reschedule-1"
TOOL_CALL_ID = "tool-call-reschedule-1"


class TrackingAppointmentReschedulingService:
    def __init__(self, inner: AppointmentReschedulingService) -> None:
        self.inner = inner
        self.reschedule_calls: list[AppointmentReschedulingRequest] = []

    def reschedule_appointment(
        self,
        request: AppointmentReschedulingRequest,
    ) -> AppointmentReschedulingResult:
        self.reschedule_calls.append(request)
        return self.inner.reschedule_appointment(request)


def reschedule_arguments(
    *,
    original_appointment_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": "Yes, please reschedule it.",
        "reschedule_reason": "Patient requested a new time",
    }
    if original_appointment_id is not None:
        payload["original_appointment_id"] = original_appointment_id
    if hold_id is not None:
        payload["hold_id"] = hold_id
    if new_slot_id is not None:
        payload["new_slot_id"] = new_slot_id
    return payload


def reschedule_tool_request(
    *,
    original_appointment_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "reschedule_appointment",
            "arguments": reschedule_arguments(
                original_appointment_id=original_appointment_id,
                hold_id=hold_id,
                new_slot_id=new_slot_id,
                explicit_confirmation=explicit_confirmation,
            ),
        },
    )


def create_retell_rescheduling_tool_context() -> dict[str, Any]:
    rescheduling_context = create_rescheduling_context()
    original_appointment = rescheduling_context.original_appointment
    hold = rescheduling_context.hold_service.create_hold(
        availability_slot_id=rescheduling_context.new_slot.id,
        doctor_id=rescheduling_context.doctor.id,
        start_time=rescheduling_context.new_slot.start_time,
        end_time=rescheduling_context.new_slot.end_time,
        owner_id=PROVIDER_CALL_ID,
    )

    conversation_repository = cast(
        FakeConversationRepository,
        rescheduling_context.conversation_service.repository,
    )
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        appointment_id=original_appointment.id,
        conversation_metadata={
            "voice_context": {
                "appointment_id": str(original_appointment.id),
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(rescheduling_context.new_slot.id),
                "start_time": rescheduling_context.new_slot.start_time.isoformat(),
                "end_time": rescheduling_context.new_slot.end_time.isoformat(),
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

    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=rescheduling_context.conversation_service,
    )

    tracking_rescheduling = TrackingAppointmentReschedulingService(
        rescheduling_context.service,
    )
    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=rescheduling_context.service.doctors,
        patients=FakePatientRepository([rescheduling_context.patient]),
        availability_slots=rescheduling_context.slot_repository,
        appointments=rescheduling_context.appointment_repository,
    )

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=rescheduling_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=rescheduling_context.conversation_service,
        appointment_rescheduling=tracking_rescheduling,
        appointments=rescheduling_context.appointment_repository,
    )

    return {
        "adapter": adapter,
        "tracking_rescheduling": tracking_rescheduling,
        "rescheduling_context": rescheduling_context,
        "conversation": conversation,
        "conversation_repository": conversation_repository,
        "original_appointment": original_appointment,
        "hold": hold,
        "new_slot": rescheduling_context.new_slot,
    }
