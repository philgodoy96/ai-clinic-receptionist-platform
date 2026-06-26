from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.domain.receptionist.enums import ReceptionistResponseMode
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.scheduling import Doctor
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingResult,
    AppointmentBookingService,
)
from app.services.appointment_holds import (
    AppointmentHoldService,
    AppointmentSlotAlreadyHeldError,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistReply,
    ChatReceptionistService,
    DeterministicChatResponder,
)
from app.services.clinic_time import ClinicTimeService
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationCreate, ConversationService
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.receptionist_response_generator import ReceptionistResponseGenerator
from app.services.scheduling import SchedulingService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from tests.test_appointment_holds import FakeAppointmentHoldRepository
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    EMILY_JULY_SLOT_2_ID,
    FakeAppointmentRepository,
    create_availability_slot,
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_afternoon_july_availability,
    create_demo_scheduling_service_with_emily_july_availability,
    create_demo_scheduling_service_with_emily_mixed_july_availability,
    create_service,
    create_specialty,
)


class FakeAppointmentHoldService(AppointmentHoldService):
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        create_hold_error: Exception | None = None,
    ) -> None:
        super().__init__(
            repository=FakeAppointmentHoldRepository(),
            ttl_seconds=ttl_seconds,
        )
        self.create_hold_error = create_hold_error
        self.create_hold_calls: list[dict[str, object]] = []

    def create_hold(
        self,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        self.create_hold_calls.append(
            {
                "availability_slot_id": availability_slot_id,
                "doctor_id": doctor_id,
                "start_time": start_time,
                "end_time": end_time,
                "owner_id": owner_id,
            }
        )
        if self.create_hold_error is not None:
            raise self.create_hold_error

        return super().create_hold(
            availability_slot_id=availability_slot_id,
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            owner_id=owner_id,
        )


class TrackingAppointmentBookingService:
    def __init__(
        self,
        inner: AppointmentBookingService,
        *,
        book_error: Exception | None = None,
    ) -> None:
        self.inner = inner
        self.book_error = book_error
        self.book_calls: list[AppointmentBookingRequest] = []

    def book_appointment(self, request: AppointmentBookingRequest) -> AppointmentBookingResult:
        self.book_calls.append(request)
        if self.book_error is not None:
            raise self.book_error
        return self.inner.book_appointment(request)


def _create_hold_service(
    *,
    create_hold_error: Exception | None = None,
) -> FakeAppointmentHoldService:
    return FakeAppointmentHoldService(create_hold_error=create_hold_error)


def create_appointment_booking_service_for_scheduling(
    scheduling: SchedulingService,
    hold_service: AppointmentHoldService,
) -> AppointmentBookingService:
    return AppointmentBookingService(
        patients=scheduling.patients,
        doctors=scheduling.doctors,
        availability_slots=scheduling.availability_slots,
        appointments=scheduling.appointments,
        hold_service=hold_service,
    )


def create_patient_identity_resolution_for_scheduling(
    scheduling: SchedulingService,
    *,
    mode: VoicePatientIntakeMode = VoicePatientIntakeMode.DEMO_AUTO_CREATE,
) -> PatientIdentityResolutionService:
    return PatientIdentityResolutionService(
        patients=scheduling.patients,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=scheduling.patients,
            mode=mode,
        ),
    )


def create_chat_receptionist_service(
    *,
    conversations: ConversationService,
    scheduling: SchedulingService,
    hold_service: FakeAppointmentHoldService | None = None,
    appointment_booking: AppointmentBookingService | None = None,
    responder: DeterministicChatResponder | None = None,
    llm_analysis: LLMReceptionistAnalysisService | None = None,
    slot_filling: LLMChatSlotFillingService | None = None,
    conversation_health: ConversationHealthService | None = None,
    human_escalations: HumanEscalationService | None = None,
    human_handoff_notifications: HumanHandoffNotificationService | None = None,
    date_parser: NaturalLanguageDateParser | None = None,
    time_preference_parser: TimePreferenceParser | None = None,
    clinic_time_service: ClinicTimeService | None = None,
    response_generator: ReceptionistResponseGenerator | None = None,
    response_generation_mode: ReceptionistResponseMode = (ReceptionistResponseMode.DETERMINISTIC),
    patient_identity_resolution: PatientIdentityResolutionService | None = None,
) -> ChatReceptionistService:
    holds = hold_service or _create_hold_service()
    booking = appointment_booking or create_appointment_booking_service_for_scheduling(
        scheduling,
        holds,
    )
    identity_resolution = (
        patient_identity_resolution
        if patient_identity_resolution is not None
        else create_patient_identity_resolution_for_scheduling(scheduling)
    )
    return ChatReceptionistService(
        conversations=conversations,
        scheduling=scheduling,
        appointment_holds=holds,
        appointment_booking=booking,
        responder=responder,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        conversation_health=conversation_health,
        human_escalations=human_escalations,
        human_handoff_notifications=human_handoff_notifications,
        date_parser=date_parser,
        time_preference_parser=time_preference_parser,
        clinic_time_service=clinic_time_service,
        response_generator=response_generator,
        response_generation_mode=response_generation_mode,
        patient_identity_resolution=identity_resolution,
    )


@pytest.fixture()
def chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )

    return service, repository


@pytest.fixture()
def scheduling_chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )

    return service, repository


@pytest.fixture()
def availability_guidance_service() -> tuple[
    ChatReceptionistService,
    FakeConversationRepository,
    FakeAppointmentHoldService,
]:
    return _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )


def _create_availability_guidance_service(
    scheduling: SchedulingService,
) -> tuple[
    ChatReceptionistService,
    FakeConversationRepository,
    FakeAppointmentHoldService,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    date_parser = NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1)))
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        date_parser=date_parser,
        time_preference_parser=TimePreferenceParser(),
    )

    return service, repository, hold_service


def _get_emily_carter(service: ChatReceptionistService) -> Doctor:
    for doctor in service.scheduling.list_doctors():
        if doctor.full_name == "Dr. Emily Carter":
            return doctor

    raise AssertionError("Dr. Emily Carter was not found in fake scheduling data")


def test_handle_message_creates_conversation_when_conversation_id_missing(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="Hello there"))

    assert len(repository.conversations) == 1
    assert result.conversation.id == repository.conversations[0].id
    assert result.conversation.channel == ConversationChannel.CHAT


def test_handle_message_reuses_existing_conversation_when_conversation_id_provided(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service
    existing = service.conversations.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="Follow up question",
            conversation_id=existing.id,
        ),
    )

    assert len(repository.conversations) == 1
    assert result.conversation.id == existing.id


def test_user_message_is_persisted_with_role_user(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="I need help"))

    assert result.user_message.role == ConversationMessageRole.USER
    assert result.user_message.content == "I need help"
    assert result.user_message in repository.messages


def test_assistant_message_is_persisted_with_role_assistant(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="Hello"))

    assert result.assistant_message.role == ConversationMessageRole.ASSISTANT
    assert result.assistant_message.content == result.reply
    assert result.assistant_message in repository.messages


def test_list_specialties_returns_intent_and_real_specialty_names(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="What specialties do you have?"),
    )

    assert result.intent == ChatReceptionistIntent.LIST_SPECIALTIES
    assert "Dermatology" in result.reply
    assert "Cardiology" in result.reply
    assert "Primary Care" in result.reply


def test_list_doctors_returns_intent_and_real_doctor_names(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="Which doctors do you have?"),
    )

    assert result.intent == ChatReceptionistIntent.LIST_DOCTORS
    assert "Dr. Emily Carter" in result.reply
    assert "Dr. Michael Reed" in result.reply
    assert "Dr. Sarah Mitchell" in result.reply


def test_dermatologist_request_matches_specialty_and_lists_doctors(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="I need a dermatologist"),
    )

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "Dr. Emily Carter" in result.reply
    assert "Dr. Michael Reed" not in result.reply
    assert result.assistant_message.message_metadata["matched_specialty_name"] == "Dermatology"
    assert result.assistant_message.message_metadata["intent"] == "specialty_doctors"


def test_appointment_request_message_returns_intent_appointment_request(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = scheduling_chat_service
    appointments = service.scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    with patch.object(appointments, "add", wraps=appointments.add) as add_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="I need an appointment"),
        )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "specialty or doctor" in result.reply.lower()
    assert result.conversation.appointment_id is None
    add_appointment_mock.assert_not_called()


def test_emergency_message_returns_intent_emergency_and_safe_guidance(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = chat_service

    result = service.handle_message(
        ChatMessageInput(message="I have chest pain and this is an emergency."),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "medical emergency" in result.reply.lower()
    assert "emergency services" in result.reply.lower()


def test_emergency_message_does_not_call_scheduling(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "list_specialties",
            wraps=scheduling.list_specialties,
        ) as list_specialties_mock,
        patch.object(
            SchedulingService,
            "list_doctors",
            wraps=scheduling.list_doctors,
        ) as list_doctors_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="This is an emergency and I have chest pain."),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    list_specialties_mock.assert_not_called()
    list_doctors_mock.assert_not_called()


def test_cancel_request_takes_priority_over_doctor_listing(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="Please cancel my appointment with the doctors office."),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST


def test_what_times_are_available_without_doctor_or_date_returns_missing_doctor(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="What times are available?"),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR
    check_availability_mock.assert_not_called()


def test_dr_emily_carter_availability_without_date_stores_doctor_and_returns_missing_date(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    emily = _get_emily_carter(service)

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter availability"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_MISSING_DATE
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_id"] == str(emily.id)
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"


def test_dr_emily_carter_on_date_returns_availability_results(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            SchedulingService,
            "get_available_slot_for_hold",
            wraps=scheduling.get_available_slot_for_hold,
        ) as hold_mock,
        patch.object(
            appointments,
            "add",
            wraps=appointments.add,
        ) as booking_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "09:00" in result.reply
    assert "10:30" in result.reply
    check_availability_mock.assert_called_once()
    hold_mock.assert_not_called()
    booking_mock.assert_not_called()

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_id"] == str(_get_emily_carter(service).id)
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert chat_context["requested_date"] == "2026-07-02"
    offered_slots = chat_context["offered_slots"]
    assert len(offered_slots) == 2
    assert offered_slots[0]["display_time"] == "09:00"
    assert offered_slots[1]["display_time"] == "10:30"
    for offered_slot in offered_slots:
        assert offered_slot["availability_slot_id"]
        assert offered_slot["doctor_id"] == str(_get_emily_carter(service).id)
        assert offered_slot["start_time"]
    assert result.assistant_message.message_metadata["availability_checked"] is True
    assert result.assistant_message.message_metadata["offered_slot_count"] == 2


def test_dermatology_on_date_auto_selects_doctor_and_returns_availability_results(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="Dermatology on 2026-07-02"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "Dr. Emily Carter" in result.reply
    assert "09:00" in result.reply
    assert "10:30" in result.reply


def test_invalid_date_does_not_query_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter on 2026-99-99"),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_DATE
    assert "YYYY-MM-DD" in result.reply
    assert "tomorrow" in result.reply
    assert result.assistant_message.message_metadata["date_parsing"]["status"] == "invalid"
    check_availability_mock.assert_not_called()


def test_dr_emily_carter_tomorrow_returns_availability_results(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter tomorrow"),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    check_availability_mock.assert_called_once()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_date"] == "2026-07-02"
    assert result.assistant_message.message_metadata["date_parsing"]["status"] == "parsed"
    assert "09:00" in result.reply


def test_dermatology_next_monday_sets_requested_date_and_queries_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dermatology next Monday"),
        )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_date"] == "2026-07-06"
    assert chat_context["selected_specialty_name"] == "Dermatology"
    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    check_availability_mock.assert_called_once()
    assert result.assistant_message.message_metadata["date_parsing"]["status"] == "parsed"


def test_unsupported_date_with_availability_request_returns_clarification(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter availability next week"),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_DATE
    assert result.reply == (
        "Please provide a specific date in YYYY-MM-DD or say something like "
        "tomorrow or next Monday."
    )
    assert result.assistant_message.message_metadata["date_parsing"]["status"] == "unsupported"
    check_availability_mock.assert_not_called()


def test_dr_emily_carter_next_week_asks_for_specific_date(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            service.appointment_holds,
            "create_hold",
            wraps=service.appointment_holds.create_hold,
        ) as create_hold_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter next week"),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_DATE
    assert result.reply == (
        "Please provide a specific date in YYYY-MM-DD or say something like "
        "tomorrow or next Monday."
    )
    assert "requested_date" not in result.conversation.conversation_metadata.get(
        "chat_context",
        {},
    )
    assert result.assistant_message.message_metadata["date_parsing"]["status"] == "unsupported"
    check_availability_mock.assert_not_called()
    create_hold_mock.assert_not_called()


def test_emergency_with_tomorrow_does_not_set_requested_date(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            service.appointment_holds,
            "create_hold",
            wraps=service.appointment_holds.create_hold,
        ) as create_hold_mock,
        patch.object(
            service.appointment_booking,
            "book_appointment",
            wraps=service.appointment_booking.book_appointment,
        ) as book_appointment_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="This is an emergency tomorrow"),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "requested_date" not in chat_context
    assert "hold_id" not in chat_context
    assert "appointment_id" not in chat_context
    assert "date_parsing" not in result.assistant_message.message_metadata
    check_availability_mock.assert_not_called()
    create_hold_mock.assert_not_called()
    book_appointment_mock.assert_not_called()


def test_date_with_no_slots_returns_availability_no_slots(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-03"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    assert result.assistant_message.message_metadata["availability_checked"] is True
    assert result.assistant_message.message_metadata["offered_slot_count"] == 0
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["offered_slots"] == []
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert chat_context["requested_date"] == "2026-07-03"


def test_emergency_takes_priority_and_does_not_query_scheduling(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            SchedulingService,
            "list_doctors",
            wraps=scheduling.list_doctors,
        ) as list_doctors_mock,
        patch.object(
            SchedulingService,
            "list_specialties",
            wraps=scheduling.list_specialties,
        ) as list_specialties_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(
                message="This is an emergency, is Dr. Emily free on 2026-07-02?",
            ),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    check_availability_mock.assert_not_called()
    list_doctors_mock.assert_not_called()
    list_specialties_mock.assert_not_called()


def test_conversation_context_carries_across_messages(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    first = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter availability"),
    )

    assert first.intent == ChatReceptionistIntent.AVAILABILITY_MISSING_DATE

    second = service.handle_message(
        ChatMessageInput(
            message="2026-07-02",
            conversation_id=first.conversation.id,
        ),
    )

    assert second.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "09:00" in second.reply
    assert "10:30" in second.reply


def test_availability_with_specialty_and_multiple_doctors_returns_specialty_wide_results(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    anna = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Anna Brooks",
        email="anna.brooks@example-clinic.test",
        phone_number="+1-555-0105",
        is_active=True,
    )
    availability_slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=anna.id,
            start_time=datetime(2026, 7, 2, 14, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    service_with_multiple_dermatologists = create_chat_receptionist_service(
        conversations=service.conversations,
        scheduling=create_service(
            specialties=[dermatology],
            doctors=[emily, anna],
            availability_slots=availability_slots,
        ),
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )

    result = service_with_multiple_dermatologists.handle_message(
        ChatMessageInput(
            message="I want to book dermatology on 2026-07-02.",
        ),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "Dr. Emily Carter" in result.reply
    assert "Dr. Anna Brooks" in result.reply
    assert "10:00" in result.reply
    assert "14:00" in result.reply
    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    assert len(offered_slots) == 2
    assert offered_slots[0]["doctor_name"] == "Dr. Emily Carter"
    assert offered_slots[1]["doctor_name"] == "Dr. Anna Brooks"


def test_doctor_specialty_and_date_uses_doctor_specific_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability_for_specialty",
        wraps=scheduling.check_availability_for_specialty,
    ) as specialty_availability_mock:
        result = service.handle_message(
            ChatMessageInput(
                message=(
                    "I want to book dermatology with Dr. Emily Carter on 2026-07-02."
                ),
            ),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    specialty_availability_mock.assert_not_called()
    assert "Dr. Emily Carter" in result.reply
    assert "09:00" in result.reply


def test_book_appointment_without_doctor_or_specialty_prompts_for_preference(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="I want to book an appointment."),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "specialty" in result.reply.lower()
    assert "doctor" in result.reply.lower()


def test_unknown_specialty_does_not_query_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        with patch.object(
            SchedulingService,
            "check_availability_for_specialty",
            wraps=scheduling.check_availability_for_specialty,
        ) as specialty_availability_mock:
            result = service.handle_message(
                ChatMessageInput(
                    message="I want to book neurosurgery on 2026-07-02.",
                ),
            )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_UNKNOWN_SPECIALTY
    assert "could not find that specialty" in result.reply.lower()
    assert "Dermatology" in result.reply
    check_availability_mock.assert_not_called()
    specialty_availability_mock.assert_not_called()


def test_unknown_doctor_does_not_query_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="I want to book with Dr. Fake Person on 2026-07-02.",
            ),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_UNKNOWN_DOCTOR
    assert "could not find that doctor" in result.reply.lower()
    check_availability_mock.assert_not_called()


def test_hold_after_specialty_wide_availability_preserves_doctor_context(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    anna = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Anna Brooks",
        email="anna.brooks@example-clinic.test",
        phone_number="+1-555-0105",
        is_active=True,
    )
    availability_slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=anna.id,
            start_time=datetime(2026, 7, 2, 14, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    hold_service = _create_hold_service()
    service_with_multiple_dermatologists = create_chat_receptionist_service(
        conversations=service.conversations,
        scheduling=create_service(
            specialties=[dermatology],
            doctors=[emily, anna],
            availability_slots=availability_slots,
        ),
        hold_service=hold_service,
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )

    availability = service_with_multiple_dermatologists.handle_message(
        ChatMessageInput(message="I want to book dermatology on 2026-07-02."),
    )
    hold = service_with_multiple_dermatologists.handle_message(
        ChatMessageInput(
            message="I'll take 14:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert hold.intent == ChatReceptionistIntent.HOLD_CREATED
    chat_context = hold.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_id"] == str(anna.id)
    assert chat_context["selected_doctor_name"] == "Dr. Anna Brooks"
    assert chat_context["selected_start_time"]
    assert "seen" in hold.reply.lower() or "before" in hold.reply.lower()


def test_specialty_with_no_doctors_returns_safe_message(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = chat_service
    empty_specialty = create_specialty(name="Pediatrics")
    service_with_empty_specialty = create_chat_receptionist_service(
        conversations=service.conversations,
        scheduling=create_service(specialties=[empty_specialty]),
    )

    result = service_with_empty_specialty.handle_message(
        ChatMessageInput(message="I am looking for pediatrics."),
    )

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "Pediatrics" in result.reply
    assert "do not currently have any doctors" in result.reply.lower()


def test_handle_message_does_not_call_llm_provider(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service
    responder = SpyDeterministicChatResponder()
    service_with_spy = create_chat_receptionist_service(
        conversations=service.conversations,
        scheduling=service.scheduling,
        responder=responder,
    )

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = service_with_spy.handle_message(
            ChatMessageInput(message="Hello"),
        )

    assert responder.call_count == 1
    assert openai_mock.call_count == 0
    assert result.conversation.appointment_id is None
    assert len(repository.conversations) == 1


def test_availability_results_store_offered_slots(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    assert len(offered_slots) == 2


def test_hold_without_offered_slots_returns_hold_missing_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="hold 09:00"),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_MISSING_AVAILABILITY
    assert result.assistant_message.message_metadata["hold_created"] is False
    assert hold_service.create_hold_calls == []


def test_hold_first_slot_at_requested_time_creates_hold(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service
    appointments = service.scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    with patch.object(
        appointments,
        "add",
        wraps=appointments.add,
    ) as booking_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="I'll take 09:00",
                conversation_id=availability.conversation.id,
            ),
        )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert len(hold_service.create_hold_calls) == 1
    booking_mock.assert_not_called()

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"]
    assert result.assistant_message.message_metadata["hold_created"] is True
    assert result.assistant_message.message_metadata["hold_id"] == chat_context["hold_id"]


def test_hold_first_one_after_availability_creates_hold(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    with patch.object(
        appointments,
        "add",
        wraps=appointments.add,
    ) as booking_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="I'll take the first one",
                conversation_id=availability.conversation.id,
            ),
        )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert "09:00" in result.reply
    assert "Dr. Emily Carter" in result.reply
    assert "seen" in result.reply.lower() or "hold" in result.reply.lower()
    booking_mock.assert_not_called()

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"]
    assert chat_context["hold_owner_id"] == str(availability.conversation.id)
    assert chat_context["selected_availability_slot_id"]
    assert chat_context["selected_start_time"]
    assert chat_context["hold_expires_at"]
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert result.assistant_message.message_metadata["hold_created"] is True
    assert result.assistant_message.message_metadata["hold_id"] == chat_context["hold_id"]


def test_hold_by_ordinal_selects_first_offered_slot(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="first one",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_availability_slot_id"] == str(EMILY_JULY_SLOT_1_ID)
    assert chat_context["selected_start_time"] == "2026-07-02T09:00:00+00:00"


def test_hold_time_match_creates_hold_for_second_slot(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="10:30 works for me",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert "10:30" in result.reply
    assert result.conversation.conversation_metadata["chat_context"][
        "selected_availability_slot_id"
    ] == str(EMILY_JULY_SLOT_2_ID)


def test_hold_unknown_time_returns_hold_slot_not_found(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="I'll take 15:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND
    assert result.assistant_message.message_metadata["hold_created"] is False
    assert hold_service.create_hold_calls == []


def test_hold_generic_request_returns_hold_request(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="Please hold a slot for me",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_REQUEST
    assert result.assistant_message.message_metadata["hold_created"] is False


def test_hold_conflict_when_fake_hold_service_raises_already_held() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    hold_service = _create_hold_service(
        create_hold_error=AppointmentSlotAlreadyHeldError(
            "slot already has an active hold",
        ),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
    )
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    with patch.object(
        appointments,
        "add",
        wraps=appointments.add,
    ) as booking_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="I'll take 09:00",
                conversation_id=availability.conversation.id,
            ),
        )

    assert result.intent == ChatReceptionistIntent.HOLD_CONFLICT
    assert result.assistant_message.message_metadata["hold_created"] is False
    assert len(hold_service.create_hold_calls) == 1
    booking_mock.assert_not_called()


def test_emergency_with_hold_keywords_does_not_create_hold(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="This is an emergency, hold 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "hold_created" not in result.assistant_message.message_metadata
    assert hold_service.create_hold_calls == []


def test_hold_created_response_uses_expected_language(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    reply = result.reply.lower()
    assert "hold" in reply
    assert "seen here before" in reply or "been seen" in reply
    assert "hold_id" not in reply


def test_morning_availability_filters_slots_and_stores_time_window(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter morning availability on 2026-07-02"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "09:00" in result.reply
    assert "10:30" in result.reply

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_time_window"] == {
        "label": "morning",
        "start_time": "08:00",
        "end_time": "12:00",
    }
    assert len(chat_context["offered_slots"]) == 2
    assert result.assistant_message.message_metadata["time_preference"]["status"] == "parsed"
    assert result.assistant_message.message_metadata["time_preference"]["label"] == "morning"


def test_evening_preference_with_only_morning_slots_returns_no_matching_window(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter evening availability on 2026-07-02"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW
    assert "evening openings" in result.reply
    assert result.assistant_message.message_metadata["availability_checked"] is True
    assert result.conversation.conversation_metadata["chat_context"]["offered_slots"] == []
    assert hold_service.create_hold_calls == []


def test_unsupported_time_preference_with_availability_request_returns_clarification(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="Dr. Emily Carter early morning availability on 2026-07-02",
            ),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_TIME_PREFERENCE
    assert "morning, afternoon, or evening" in result.reply
    assert result.assistant_message.message_metadata["time_preference"]["status"] == "unsupported"
    check_availability_mock.assert_not_called()


def test_ambiguous_time_preference_with_availability_request_returns_clarification(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="Dr. Emily Carter morning or afternoon availability on 2026-07-02",
            ),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_TIME_PREFERENCE
    assert result.assistant_message.message_metadata["time_preference"]["status"] == "ambiguous"
    check_availability_mock.assert_not_called()


def test_emergency_tomorrow_morning_does_not_apply_time_preference_or_query_scheduling(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            service.appointment_holds,
            "create_hold",
            wraps=service.appointment_holds.create_hold,
        ) as create_hold_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="This is an emergency tomorrow morning"),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "requested_time_window" not in chat_context
    assert "time_preference" not in result.assistant_message.message_metadata
    check_availability_mock.assert_not_called()
    create_hold_mock.assert_not_called()


def test_hold_at_exact_time_still_works_after_morning_filtered_availability(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter morning availability on 2026-07-02"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert len(hold_service.create_hold_calls) == 1


def test_changing_time_preference_without_hold_updates_requested_time_window(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    first = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter morning availability on 2026-07-02"),
    )

    second = service.handle_message(
        ChatMessageInput(
            message="afternoon",
            conversation_id=first.conversation.id,
        ),
    )

    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_time_window"] == {
        "label": "afternoon",
        "start_time": "12:00",
        "end_time": "17:00",
    }


def test_changing_time_preference_with_active_hold_requests_clarification(
    availability_guidance_service: tuple[
        ChatReceptionistService,
        FakeConversationRepository,
        FakeAppointmentHoldService,
    ],
) -> None:
    service, _repository, _hold_service = availability_guidance_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter morning availability on 2026-07-02"),
    )
    held = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="afternoon",
            conversation_id=held.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_REQUEST
    assert "already have a time held" in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_time_window"]["label"] == "morning"


def test_dr_emily_carter_tomorrow_morning_parses_date_and_filters_morning_slots() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter tomorrow morning"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert result.assistant_message.message_metadata["date_parsing"]["normalized_date"] == (
        "2026-07-02"
    )
    assert result.assistant_message.message_metadata["time_preference"]["label"] == "morning"

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_date"] == "2026-07-02"
    assert chat_context["requested_time_window"]["label"] == "morning"
    assert len(chat_context["offered_slots"]) == 2
    assert chat_context["offered_slots"][0]["display_time"] == "09:00"
    assert chat_context["offered_slots"][1]["display_time"] == "10:30"
    assert "hold_id" not in chat_context
    assert hold_service.create_hold_calls == []


def test_dr_emily_carter_tomorrow_afternoon_offers_only_afternoon_slots() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_mixed_july_availability(),
    )

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter tomorrow afternoon"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "14:00" in result.reply
    assert "09:00" not in result.reply
    assert "10:30" not in result.reply

    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    assert len(offered_slots) == 1
    assert offered_slots[0]["display_time"] == "14:00"
    assert hold_service.create_hold_calls == []


def test_dr_emily_carter_tomorrow_morning_with_only_afternoon_slots_returns_no_openings() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_afternoon_july_availability(),
    )

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter tomorrow morning"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW
    assert "morning openings" in result.reply

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["offered_slots"] == []
    assert hold_service.create_hold_calls == []


def test_dr_emily_carter_tomorrow_morning_or_afternoon_is_ambiguous() -> None:
    service, _repository, _hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Emily Carter tomorrow morning or afternoon"),
        )

    assert result.intent == ChatReceptionistIntent.INVALID_TIME_PREFERENCE
    assert "morning, afternoon, or evening" in result.reply
    assert result.assistant_message.message_metadata["time_preference"]["status"] == "ambiguous"
    check_availability_mock.assert_not_called()


def test_emergency_tomorrow_morning_wins_without_scheduling_side_effects() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )
    scheduling = service.scheduling

    with (
        patch.object(
            SchedulingService,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
        patch.object(
            service.appointment_holds,
            "create_hold",
            wraps=service.appointment_holds.create_hold,
        ) as create_hold_mock,
        patch.object(
            service.appointment_booking,
            "book_appointment",
            wraps=service.appointment_booking.book_appointment,
        ) as book_appointment_mock,
    ):
        result = service.handle_message(
            ChatMessageInput(message="This is an emergency tomorrow morning"),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "requested_time_window" not in chat_context
    assert "hold_id" not in chat_context
    assert "appointment_id" not in chat_context
    check_availability_mock.assert_not_called()
    create_hold_mock.assert_not_called()
    book_appointment_mock.assert_not_called()


def test_hold_at_exact_time_after_tomorrow_morning_filtered_availability() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter tomorrow morning"),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert len(hold_service.create_hold_calls) == 1
    assert hold_service.create_hold_calls[0]["availability_slot_id"] == EMILY_JULY_SLOT_1_ID


class SpyDeterministicChatResponder(DeterministicChatResponder):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    def generate_reply(self, *, message: str) -> ChatReceptionistReply:
        self.call_count += 1

        return super().generate_reply(message=message)
