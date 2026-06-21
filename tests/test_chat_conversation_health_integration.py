from __future__ import annotations

from collections.abc import Generator
from datetime import date
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.fake_llm_provider import FakeLLMProvider
from app.api.dependencies import get_chat_receptionist_service
from app.db.session import get_db
from app.domain.conversations.enums import ConversationStatus
from app.domain.jobs.enums import EmailJobType
from app.main import create_app
from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.email_jobs import EmailJobService
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.slot_filling import LLMChatSlotFillingService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_human_escalations import FakeHumanEscalationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_july_availability,
)

FULL_IDENTITY_WITH_CONFIRM = (
    "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com. Please confirm."
)
_HUMAN_HANDOFF_PHRASE = "human follow-up"
_ESCALATION_SUGGESTION_PHRASE = "if you prefer, i can transfer this to a human receptionist"


def create_jane_doe_patient() -> Patient:
    return Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1 555-123-4567",
        email="jane.doe@example.com",
    )


@pytest.fixture()
def health_enabled_human_escalation_service() -> tuple[
    ChatReceptionistService,
    FakeHumanEscalationRepository,
    TrackingAppointmentBookingService,
    FakeAppointmentHoldService,
    FakeEmailJobRepository,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    escalation_repository = FakeHumanEscalationRepository()
    human_escalations = HumanEscalationService(repository=escalation_repository)
    email_job_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_job_repository)
    human_handoff_notifications = HumanHandoffNotificationService(
        email_jobs=email_jobs,
    )
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[create_jane_doe_patient()],
    )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
        conversation_health=ConversationHealthService(),
        human_escalations=human_escalations,
        human_handoff_notifications=human_handoff_notifications,
    )
    return (
        service,
        escalation_repository,
        tracking_booking,
        hold_service,
        email_job_repository,
    )


@pytest.fixture()
def health_enabled_booking_service() -> tuple[
    ChatReceptionistService,
    TrackingAppointmentBookingService,
    FakeAppointmentHoldService,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[create_jane_doe_patient()],
    )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
        conversation_health=ConversationHealthService(),
    )
    return service, tracking_booking, hold_service


@pytest.fixture()
def health_enabled_chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        conversation_health=ConversationHealthService(),
    )
    return service, repository


@pytest.fixture()
def health_llm_chat_service() -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_analysis = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
    )
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        conversation_health=ConversationHealthService(),
    )


def _conversation_with_active_hold(service: ChatReceptionistService) -> Conversation:
    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )
    return hold.conversation


def test_booking_flow_still_works_with_conversation_health(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, tracking_booking, _hold_service = health_enabled_booking_service
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["signals"]["active_hold_present"] is False
    assert health_metadata["signals"]["booking_confirmed"] is True
    assert "patient_identity" not in str(health_metadata)


def test_human_request_does_not_create_booking(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, tracking_booking, _hold_service = health_enabled_booking_service
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message="Please connect me to a human receptionist",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert "human receptionist" in result.reply.lower()
    assert tracking_booking.book_calls == []
    assert result.conversation.status == ConversationStatus.ESCALATED
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_escalate_immediately"] is True
    assert health_metadata["escalation_reason"] == "user_requested_human"


def test_message_count_alone_does_not_trigger_escalation(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _tracking_booking, _hold_service = health_enabled_booking_service

    conversation_id = None
    for _ in range(14):
        payload = ChatMessageInput(
            message="What specialties do you offer?",
            conversation_id=conversation_id,
        )
        result = service.handle_message(payload)
        conversation_id = result.conversation.id

    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_escalate_immediately"] is False
    assert health_metadata["should_suggest_escalation"] is False
    assert result.intent == ChatReceptionistIntent.LIST_SPECIALTIES
    assert result.conversation.status == ConversationStatus.ACTIVE


def test_emergency_response_wins_over_health_escalation_metadata(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _tracking_booking, _hold_service = health_enabled_booking_service

    result = service.handle_message(
        ChatMessageInput(message="This is an emergency, I have chest pain"),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "emergency" in result.reply.lower()
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_escalate_immediately"] is True
    assert health_metadata["escalation_reason"] == "medical_emergency"
    assert result.conversation.status == ConversationStatus.ACTIVE


def test_assistant_message_metadata_includes_conversation_health(
    health_enabled_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = health_enabled_chat_service

    result = service.handle_message(
        ChatMessageInput(message="What specialties do you offer?"),
    )

    metadata = result.assistant_message.message_metadata
    assert "conversation_health" in metadata
    health = metadata["conversation_health"]
    assert "signals" in health
    assert "should_escalate_immediately" in health
    assert "escalation_reason" in health


def test_can_i_speak_to_a_real_person_creates_human_escalation_with_handoff_metadata(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, tracking_booking, hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    result = service.handle_message(
        ChatMessageInput(message="Can I speak to a real person?"),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert _HUMAN_HANDOFF_PHRASE in result.reply.lower()
    assert len(escalation_repository.escalations) == 1
    assert len(email_job_repository.email_jobs) == 1
    assert tracking_booking.book_calls == []
    assert hold_service.create_hold_calls == []
    assert result.appointment_id is None
    assert result.booking_confirmed is False
    assert "hold_created" not in result.assistant_message.message_metadata

    email_job = email_job_repository.email_jobs[0]
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert result.human_handoff_notification_email_job_id == email_job.id

    metadata = result.assistant_message.message_metadata["human_escalation"]
    assert metadata["created"] is True
    assert metadata["reason"] == "user_requested_human"
    assert metadata["priority"] == "high"
    assert metadata["status"] == "open"
    assert metadata["escalation_id"] == str(escalation_repository.escalations[0].id)

    notification_metadata = result.assistant_message.message_metadata[
        "human_handoff_notification"
    ]
    assert notification_metadata["created"] is True
    assert notification_metadata["email_job_id"] == str(email_job.id)


def test_human_request_returns_handoff_without_booking_or_hold(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, tracking_booking, hold_service = health_enabled_booking_service

    result = service.handle_message(
        ChatMessageInput(message="Can I speak to a real person?"),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert _HUMAN_HANDOFF_PHRASE in result.reply.lower()
    assert tracking_booking.book_calls == []
    assert hold_service.create_hold_calls == []
    assert result.conversation.status == ConversationStatus.ESCALATED
    assert "hold_created" not in result.assistant_message.message_metadata
    assert result.appointment_id is None


def test_emergency_records_medical_emergency_reason(
    health_enabled_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = health_enabled_chat_service

    result = service.handle_message(
        ChatMessageInput(message="This is an emergency"),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_escalate_immediately"] is True
    assert health_metadata["escalation_reason"] == "medical_emergency"
    assert result.conversation.status == ConversationStatus.ACTIVE


def test_suggested_escalation_appends_soft_handoff_for_repeated_fallback(
    health_enabled_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = health_enabled_chat_service

    conversation_id = None
    for message in ("xyzzy one", "xyzzy two", "xyzzy three", "xyzzy four"):
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation_id),
        )
        conversation_id = result.conversation.id

    assert result.intent == ChatReceptionistIntent.ESCALATION_SUGGESTED
    assert _ESCALATION_SUGGESTION_PHRASE in result.reply.lower()
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_suggest_escalation"] is True
    assert health_metadata["escalation_reason"] == "repeated_fallback"


def test_booking_confirmation_does_not_append_escalation_suggestion(
    health_enabled_booking_service: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _tracking_booking, _hold_service = health_enabled_booking_service
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert _ESCALATION_SUGGESTION_PHRASE not in result.reply.lower()
    health_metadata = result.assistant_message.message_metadata["conversation_health"]
    assert health_metadata["should_suggest_escalation"] is False


def test_llm_and_slot_filling_metadata_persist_with_conversation_health(
    health_llm_chat_service: ChatReceptionistService,
) -> None:
    result = health_llm_chat_service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )

    metadata = result.assistant_message.message_metadata
    assert "conversation_health" in metadata
    assert "llm_shadow_analysis" in metadata
    assert "slot_filling" in metadata


@pytest.fixture()
def health_chat_api_client() -> Generator[
    tuple[TestClient, FakeConversationRepository],
    None,
    None,
]:
    app = create_app()
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    chat_service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=create_demo_scheduling_service(),
        hold_service=FakeAppointmentHoldService(),
        conversation_health=ConversationHealthService(),
    )

    class FakeDatabaseSession:
        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client, repository

    app.dependency_overrides.clear()


def test_chat_api_includes_conversation_health_metadata(
    health_chat_api_client: tuple[TestClient, FakeConversationRepository],
) -> None:
    client, repository = health_chat_api_client
    response = client.post(
        "/api/v1/chat/messages",
        json={"message": "What specialties do you offer?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assistant_message_id = payload["assistant_message_id"]
    assistant_message = next(
        message for message in repository.messages if str(message.id) == assistant_message_id
    )
    assistant_metadata = assistant_message.message_metadata
    assert "conversation_health" in assistant_metadata
    assert assistant_metadata["conversation_health"]["escalation_reason"] == "none"


def test_explicit_human_request_creates_human_escalation_once(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, tracking_booking, hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )

    result = service.handle_message(
        ChatMessageInput(message="Please connect me to a human receptionist"),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert len(escalation_repository.escalations) == 1
    assert tracking_booking.book_calls == []
    assert hold_service.create_hold_calls == []

    metadata = result.assistant_message.message_metadata["human_escalation"]
    assert metadata["created"] is True
    assert metadata["reason"] == "user_requested_human"
    assert metadata["priority"] == "high"
    assert metadata["status"] == "open"
    assert metadata["escalation_id"] == str(escalation_repository.escalations[0].id)


def test_repeated_human_request_reuses_active_human_escalation(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    first = service.handle_message(
        ChatMessageInput(message="Please connect me to a human receptionist"),
    )
    second = service.handle_message(
        ChatMessageInput(
            message="I still need to speak to a human receptionist",
            conversation_id=first.conversation.id,
        ),
    )

    assert len(escalation_repository.escalations) == 1
    assert len(email_job_repository.email_jobs) == 1
    second_metadata = second.assistant_message.message_metadata["human_escalation"]
    assert second_metadata["created"] is False
    assert second_metadata["escalation_id"] == str(escalation_repository.escalations[0].id)

    second_notification = second.assistant_message.message_metadata[
        "human_handoff_notification"
    ]
    assert second_notification["created"] is False
    assert second_notification["email_job_id"] == str(email_job_repository.email_jobs[0].id)


def test_human_request_with_active_hold_records_handoff_context_without_releasing_hold(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, tracking_booking, hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )
    conversation = _conversation_with_active_hold(service)
    chat_context = conversation.conversation_metadata["chat_context"]

    result = service.handle_message(
        ChatMessageInput(
            message="Please connect me to a human receptionist",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert len(escalation_repository.escalations) == 1
    assert len(hold_service.create_hold_calls) == 1
    assert tracking_booking.book_calls == []
    assert result.pending_hold_release is None
    assert result.hold_id_to_release is None

    escalation = escalation_repository.escalations[0]
    handoff_context = escalation.handoff_context
    assert handoff_context is not None
    assert handoff_context["active_hold_present"] is True
    assert handoff_context["hold_id"] == chat_context["hold_id"]
    assert handoff_context["hold_expires_at"] == chat_context["hold_expires_at"]
    assert handoff_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert handoff_context["requested_date"] == "2026-07-02"
    assert handoff_context["selected_start_time"] == chat_context["selected_start_time"]
    assert "patient_identity" not in handoff_context
    assert conversation.conversation_metadata["chat_context"]["hold_id"] == chat_context["hold_id"]


def test_emergency_creates_urgent_human_escalation(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )

    result = service.handle_message(
        ChatMessageInput(message="This is an emergency, I have chest pain"),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert len(escalation_repository.escalations) == 1
    escalation = escalation_repository.escalations[0]
    assert escalation.reason.value == "medical_emergency"
    assert escalation.priority.value == "urgent"

    metadata = result.assistant_message.message_metadata["human_escalation"]
    assert metadata["created"] is True
    assert metadata["reason"] == "medical_emergency"
    assert metadata["priority"] == "urgent"


def test_suggested_escalation_only_does_not_create_human_escalation(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )

    conversation_id = None
    for message in ("xyzzy one", "xyzzy two", "xyzzy three", "xyzzy four"):
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation_id),
        )
        conversation_id = result.conversation.id

    assert result.intent == ChatReceptionistIntent.ESCALATION_SUGGESTED
    assert escalation_repository.escalations == []
    assert "human_escalation" not in result.assistant_message.message_metadata


def test_booking_confirmation_flow_still_passes_with_human_escalation_service(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, tracking_booking, _hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert escalation_repository.escalations == []
    assert "human_escalation" not in result.assistant_message.message_metadata


def test_booking_flow_does_not_create_human_escalation(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, tracking_booking, _hold_service, _email_job_repository = (
        health_enabled_human_escalation_service
    )
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert len(tracking_booking.book_calls) == 1
    assert escalation_repository.escalations == []
    assert "human_escalation" not in result.assistant_message.message_metadata


def test_immediate_human_request_enqueues_one_notification_job(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    result = service.handle_message(
        ChatMessageInput(message="Please connect me to a human receptionist"),
    )

    assert len(escalation_repository.escalations) == 1
    assert len(email_job_repository.email_jobs) == 1
    email_job = email_job_repository.email_jobs[0]
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert result.human_handoff_notification_email_job_id == email_job.id

    notification_metadata = result.assistant_message.message_metadata[
        "human_handoff_notification"
    ]
    assert notification_metadata["created"] is True
    assert notification_metadata["email_job_id"] == str(email_job.id)
    assert email_job.payload["priority"] == "high"


def test_repeated_human_request_reuses_existing_notification_job(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, _escalation_repository, _tracking_booking, _hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    first = service.handle_message(
        ChatMessageInput(message="Please connect me to a human receptionist"),
    )
    second = service.handle_message(
        ChatMessageInput(
            message="I still need to speak to a human receptionist",
            conversation_id=first.conversation.id,
        ),
    )

    assert len(email_job_repository.email_jobs) == 1
    email_job = email_job_repository.email_jobs[0]
    assert first.human_handoff_notification_email_job_id == email_job.id
    assert second.human_handoff_notification_email_job_id == email_job.id

    first_notification = first.assistant_message.message_metadata[
        "human_handoff_notification"
    ]
    second_notification = second.assistant_message.message_metadata[
        "human_handoff_notification"
    ]
    assert first_notification["created"] is True
    assert second_notification["created"] is False
    assert second_notification["email_job_id"] == first_notification["email_job_id"]


def test_emergency_enqueues_urgent_notification_job(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    result = service.handle_message(
        ChatMessageInput(message="This is an emergency, I have chest pain"),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert len(escalation_repository.escalations) == 1
    escalation = escalation_repository.escalations[0]
    assert escalation.reason.value == "medical_emergency"
    assert escalation.priority.value == "urgent"
    assert len(email_job_repository.email_jobs) == 1
    email_job = email_job_repository.email_jobs[0]
    assert email_job.payload["priority"] == "urgent"
    assert email_job.payload["reason"] == "medical_emergency"
    assert result.human_handoff_notification_email_job_id == email_job.id
    assert "emergency" in result.reply.lower()


def test_suggested_escalation_only_does_not_enqueue_notification_job(
    health_enabled_human_escalation_service: tuple[
        ChatReceptionistService,
        FakeHumanEscalationRepository,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        FakeEmailJobRepository,
    ],
) -> None:
    service, escalation_repository, _tracking_booking, _hold_service, email_job_repository = (
        health_enabled_human_escalation_service
    )

    conversation_id = None
    for message in ("xyzzy one", "xyzzy two", "xyzzy three", "xyzzy four"):
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation_id),
        )
        conversation_id = result.conversation.id

    assert result.intent == ChatReceptionistIntent.ESCALATION_SUGGESTED
    assert escalation_repository.escalations == []
    assert email_job_repository.email_jobs == []
    assert result.human_handoff_notification_email_job_id is None
    assert "human_handoff_notification" not in result.assistant_message.message_metadata
