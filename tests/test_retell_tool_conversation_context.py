from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_conversation import read_voice_context
from app.models.conversations import Conversation
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_holds import AppointmentHoldService
from app.services.conversations import ConversationService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import (
    NeverCalledBookingService,
    NeverCalledEmailService,
    NeverCalledLLMService,
    TrackingAppointmentHoldRepository,
    TrackingSchedulingService,
)


@pytest.fixture()
def doctor_id() -> UUID:
    return uuid4()


@pytest.fixture()
def slot_id() -> UUID:
    return uuid4()


@pytest.fixture()
def availability_slot(doctor_id: UUID, slot_id: UUID) -> AvailabilitySlot:
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    return AvailabilitySlot(
        id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )


@pytest.fixture()
def context_bundle(
    doctor_id: UUID,
    availability_slot: AvailabilitySlot,
) -> ContextBundle:
    specialty_id = uuid4()
    specialty = Specialty(
        id=specialty_id,
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=doctor_id,
        specialty_id=specialty_id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    scheduling_service = TrackingSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        availability_slots=[availability_slot],
    )
    hold_repository = TrackingAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    voice_calls = FakeVoiceCallRepository()
    conversations = FakeConversationRepository()
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversations,
        conversation_service=ConversationService(repository=conversations),
    )
    conversation_service = ConversationService(repository=conversations)
    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=hold_service,
        voice_calls=voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversation_service,
    )

    return ContextBundle(
        adapter=adapter,
        scheduling_service=scheduling_service,
        hold_repository=hold_repository,
        voice_calls=voice_calls,
        conversations=conversations,
        booking_service=NeverCalledBookingService(),
        email_service=NeverCalledEmailService(),
        llm_service=NeverCalledLLMService(),
        availability_slot=availability_slot,
        doctor_id=doctor_id,
    )


class ContextBundle:
    def __init__(
        self,
        *,
        adapter: RetellToolCallingAdapter,
        scheduling_service: TrackingSchedulingService,
        hold_repository: TrackingAppointmentHoldRepository,
        voice_calls: FakeVoiceCallRepository,
        conversations: FakeConversationRepository,
        booking_service: NeverCalledBookingService,
        email_service: NeverCalledEmailService,
        llm_service: NeverCalledLLMService,
        availability_slot: AvailabilitySlot,
        doctor_id: UUID,
    ) -> None:
        self.adapter = adapter
        self.scheduling_service = scheduling_service
        self.hold_repository = hold_repository
        self.voice_calls = voice_calls
        self.conversations = conversations
        self.booking_service = booking_service
        self.email_service = email_service
        self.llm_service = llm_service
        self.availability_slot = availability_slot
        self.doctor_id = doctor_id


def _seed_voice_call(
    bundle: ContextBundle,
    *,
    provider_call_id: str = "retell-call-context",
) -> VoiceCall:
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=provider_call_id,
        status=VoiceCallStatus.IN_PROGRESS,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    bundle.voice_calls.voice_calls.append(voice_call)
    return voice_call


def test_retell_hold_creates_voice_conversation_context(
    context_bundle: ContextBundle,
) -> None:
    _seed_voice_call(context_bundle)

    response = context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-context",
                "tool_call_id": "hold-context-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(context_bundle.availability_slot.id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(context_bundle.conversations.conversations) == 1
    conversation = context_bundle.conversations.conversations[0]
    assert conversation.channel == ConversationChannel.VOICE
    voice_context = read_voice_context(conversation.conversation_metadata)
    assert voice_context["hold_id"] == response.result["hold_id"]
    assert voice_context["availability_slot_id"] == str(
        context_bundle.availability_slot.id,
    )
    assert context_bundle.voice_calls.voice_calls[0].conversation_id == conversation.id


def test_duplicate_hold_does_not_create_duplicate_context_or_hold(
    context_bundle: ContextBundle,
) -> None:
    _seed_voice_call(context_bundle)
    request = RetellToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-context",
            "tool_call_id": "hold-context-dup",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(context_bundle.availability_slot.id),
            },
        },
    )

    first = context_bundle.adapter.execute(request)
    second = context_bundle.adapter.execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context_bundle.hold_repository.create_calls) == 1
    assert len(context_bundle.conversations.conversations) == 1
    voice_context = read_voice_context(
        context_bundle.conversations.conversations[0].conversation_metadata,
    )
    assert voice_context["hold_id"] == first.result["hold_id"]


def test_release_clears_active_hold_context(context_bundle: ContextBundle) -> None:
    voice_call = _seed_voice_call(context_bundle, provider_call_id="retell-call-release")
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "source": "retell_voice",
            "voice_context": {
                "hold_id": "pending",
                "availability_slot_id": str(context_bundle.availability_slot.id),
                "doctor_id": str(context_bundle.doctor_id),
                "requested_date": "2026-07-01",
            },
        },
    )
    context_bundle.conversations.conversations.append(conversation)
    voice_call.conversation_id = conversation.id

    hold_response = context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-release",
                "tool_call_id": "hold-before-release",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(context_bundle.availability_slot.id),
                },
            },
        ),
    )
    hold_id = hold_response.result["hold_id"]

    release_response = context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-release",
                "tool_call_id": "release-context-1",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": hold_id,
                },
            },
        ),
    )

    assert release_response.status == "succeeded"
    voice_context = read_voice_context(conversation.conversation_metadata)
    assert "hold_id" not in voice_context
    assert "availability_slot_id" not in voice_context
    assert voice_context["requested_date"] == "2026-07-01"


def test_check_availability_uses_voice_context_safely(
    context_bundle: ContextBundle,
) -> None:
    voice_call = _seed_voice_call(context_bundle, provider_call_id="retell-call-check")
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={
            "voice_context": {
                "requested_date": "2026-07-01",
                "doctor_id": str(context_bundle.doctor_id),
                "specialty_name": "Dermatology",
                "transcript": "secret symptoms",
            },
        },
    )
    context_bundle.conversations.conversations.append(conversation)
    voice_call.conversation_id = conversation.id

    response = context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-check",
                "tool_name": "check_availability",
                "arguments": {},
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(context_bundle.scheduling_service.check_availability_calls) == 1
    assert (
        context_bundle.scheduling_service.check_availability_calls[0].doctor_id
        == context_bundle.doctor_id
    )
    voice_context = read_voice_context(conversation.conversation_metadata)
    assert voice_context["requested_date"] == "2026-07-01"
    assert voice_context["specialty_name"] == "Dermatology"
    assert "transcript" not in voice_context
    assert "secret symptoms" not in str(conversation.conversation_metadata)


def test_no_booking_email_or_llm_side_effects(context_bundle: ContextBundle) -> None:
    _seed_voice_call(context_bundle, provider_call_id="retell-call-safe")

    context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-safe",
                "tool_call_id": "hold-safe",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(context_bundle.availability_slot.id),
                },
            },
        ),
    )
    context_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-safe",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(context_bundle.doctor_id),
                    "start_from": "2026-07-01T09:00:00Z",
                    "start_to": "2026-07-01T12:00:00Z",
                },
            },
        ),
    )

    assert context_bundle.email_service.calls == []
    assert context_bundle.llm_service.calls == []
