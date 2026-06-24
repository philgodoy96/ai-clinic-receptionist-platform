from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.voice_conversation import (
    ConversationNotFoundForBridgeError,
    VoiceConversationLinkConflictError,
)
from app.models.conversations import Conversation
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_holds import AppointmentHoldService
from app.services.conversations import ConversationNotFoundError, ConversationService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import (
    TrackingAppointmentHoldRepository,
    TrackingSchedulingService,
)

MANUAL_RETELL_CHECK_AVAILABILITY_ARGUMENTS = {
    "limit": 3,
    "doctor_id": None,
    "doctor_name": None,
    "date_expression": {
        "weekday": "wednesday",
        "exact_date": None,
        "days_offset": None,
        "kind": "tomorrow",
    },
    "time_window_expression": None,
    "specialty_name": "Dermatology",
}


@pytest.fixture()
def dermatology_bundle() -> HardeningBundle:
    doctor_id = uuid4()
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
        availability_slots=[],
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
        clinic_time_service=make_test_clinic_time_service(),
    )

    return HardeningBundle(
        adapter=adapter,
        scheduling_service=scheduling_service,
        voice_calls=voice_calls,
        conversations=conversations,
        bridge=bridge,
        conversation_service=conversation_service,
        doctor_id=doctor_id,
    )


class HardeningBundle:
    def __init__(
        self,
        *,
        adapter: RetellToolCallingAdapter,
        scheduling_service: TrackingSchedulingService,
        voice_calls: FakeVoiceCallRepository,
        conversations: FakeConversationRepository,
        bridge: VoiceConversationBridgeService,
        conversation_service: ConversationService,
        doctor_id: UUID,
    ) -> None:
        self.adapter = adapter
        self.scheduling_service = scheduling_service
        self.voice_calls = voice_calls
        self.conversations = conversations
        self.bridge = bridge
        self.conversation_service = conversation_service
        self.doctor_id = doctor_id


def _seed_voice_call(bundle: HardeningBundle, *, provider_call_id: str) -> VoiceCall:
    voice_call, _created = bundle.voice_calls.get_or_create_voice_call(
        provider="retell",
        provider_call_id=provider_call_id,
    )
    return voice_call


def test_manual_retell_check_availability_payload_succeeds_with_empty_slots(
    dermatology_bundle: HardeningBundle,
) -> None:
    _seed_voice_call(dermatology_bundle, provider_call_id="retell-manual-check")

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-manual-check",
                "tool_call_id": "tool-check-1",
                "tool_name": "check_availability",
                "arguments": MANUAL_RETELL_CHECK_AVAILABILITY_ARGUMENTS,
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.error_code is None
    assert response.result["available_slots"] == []
    assert response.result["doctor_id"] == str(dermatology_bundle.doctor_id)
    assert len(dermatology_bundle.scheduling_service.check_availability_calls) == 1


def test_check_availability_tomorrow_with_redundant_weekday_succeeds(
    dermatology_bundle: HardeningBundle,
) -> None:
    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-redundant-weekday",
                "tool_name": "check_availability",
                "arguments": {
                    "specialty_name": "Dermatology",
                    "date_expression": {
                        "kind": "tomorrow",
                        "weekday": "wednesday",
                    },
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(dermatology_bundle.scheduling_service.check_availability_calls) == 1


def test_check_availability_null_time_window_expression_succeeds(
    dermatology_bundle: HardeningBundle,
) -> None:
    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-null-time-window",
                "tool_name": "check_availability",
                "arguments": {
                    "specialty_name": "Dermatology",
                    "date_expression": {"kind": "tomorrow"},
                    "time_window_expression": None,
                },
            },
        ),
    )

    assert response.status == "succeeded"


def test_check_availability_specialty_name_resolves_doctor(
    dermatology_bundle: HardeningBundle,
) -> None:
    start_time = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    dermatology_bundle.scheduling_service.availability_slots.append(
        AvailabilitySlot(
            id=uuid4(),
            doctor_id=dermatology_bundle.doctor_id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-specialty-resolve",
                "tool_name": "check_availability",
                "arguments": {
                    "specialty_name": "Dermatology",
                    "date_expression": {"kind": "tomorrow"},
                    "limit": 3,
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(response.result["available_slots"]) == 1
    assert (
        dermatology_bundle.scheduling_service.check_availability_calls[0].doctor_id
        == dermatology_bundle.doctor_id
    )


def test_voice_conversation_link_conflict_does_not_fail_check_availability(
    dermatology_bundle: HardeningBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_link_conflict(*_args: object, **_kwargs: object) -> Conversation:
        raise VoiceConversationLinkConflictError(
            "voice call is already linked to a different conversation",
        )

    monkeypatch.setattr(
        dermatology_bundle.bridge,
        "ensure_conversation_for_tool_callback",
        _raise_link_conflict,
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-link-conflict",
                "tool_name": "check_availability",
                "arguments": MANUAL_RETELL_CHECK_AVAILABILITY_ARGUMENTS,
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result["available_slots"] == []


def test_conversation_not_found_during_context_update_does_not_fail_check_availability(
    dermatology_bundle: HardeningBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice_call = _seed_voice_call(dermatology_bundle, provider_call_id="retell-context-update")
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={"voice_context": {}},
    )
    dermatology_bundle.conversations.conversations.append(conversation)
    voice_call.conversation_id = conversation.id

    def _raise_not_found(*_args: object, **_kwargs: object) -> Conversation:
        raise ConversationNotFoundError("conversation was not found")

    monkeypatch.setattr(
        dermatology_bundle.conversation_service,
        "merge_voice_context",
        _raise_not_found,
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-context-update",
                "tool_name": "check_availability",
                "arguments": {
                    "specialty_name": "Dermatology",
                    "doctor_id": str(dermatology_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                },
            },
        ),
    )

    assert response.status == "succeeded"


def test_scheduling_database_failure_still_fails_check_availability(
    dermatology_bundle: HardeningBundle,
) -> None:
    dermatology_bundle.scheduling_service.check_availability_error = OperationalError(
        "SELECT 1",
        {},
        Exception("database unavailable"),
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-db-failure",
                "tool_name": "check_availability",
                "arguments": {
                    "specialty_name": "Dermatology",
                    "date_expression": {"kind": "tomorrow"},
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "retell_tool_execution_failed"


def test_bridge_conversation_not_found_during_ensure_does_not_fail_check_availability(
    dermatology_bundle: HardeningBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_not_found(*_args: object, **_kwargs: object) -> Conversation:
        raise ConversationNotFoundForBridgeError("conversation was not found")

    monkeypatch.setattr(
        dermatology_bundle.bridge,
        "ensure_conversation_for_tool_callback",
        _raise_not_found,
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-bridge-not-found",
                "tool_name": "check_availability",
                "arguments": MANUAL_RETELL_CHECK_AVAILABILITY_ARGUMENTS,
            },
        ),
    )

    assert response.status == "succeeded"


def test_hold_still_fails_on_voice_conversation_link_conflict(
    dermatology_bundle: HardeningBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_id = uuid4()
    start_time = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    dermatology_bundle.scheduling_service.availability_slots.append(
        AvailabilitySlot(
            id=slot_id,
            doctor_id=dermatology_bundle.doctor_id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    )

    def _raise_link_conflict(*_args: object, **_kwargs: object) -> Conversation:
        raise VoiceConversationLinkConflictError(
            "voice call is already linked to a different conversation",
        )

    monkeypatch.setattr(
        dermatology_bundle.bridge,
        "ensure_conversation_for_tool_callback",
        _raise_link_conflict,
    )

    response = dermatology_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-hold-link-conflict",
                "tool_call_id": "hold-tool-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": str(slot_id)},
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "retell_tool_execution_failed"
