from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.models.scheduling import Doctor
from app.services.appointment_booking import AppointmentBookingService
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
from app.services.conversations import ConversationCreate, ConversationService
from app.services.scheduling import SchedulingService
from tests.test_appointment_holds import FakeAppointmentHoldRepository
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    EMILY_JULY_SLOT_2_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_july_availability,
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


def create_chat_receptionist_service(
    *,
    conversations: ConversationService,
    scheduling: SchedulingService,
    hold_service: FakeAppointmentHoldService | None = None,
    responder: DeterministicChatResponder | None = None,
) -> ChatReceptionistService:
    holds = hold_service or _create_hold_service()
    booking = create_appointment_booking_service_for_scheduling(scheduling, holds)
    if responder is None:
        return ChatReceptionistService(
            conversations=conversations,
            scheduling=scheduling,
            appointment_holds=holds,
            appointment_booking=booking,
        )
    return ChatReceptionistService(
        conversations=conversations,
        scheduling=scheduling,
        appointment_holds=holds,
        appointment_booking=booking,
        responder=responder,
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
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    hold_service = _create_hold_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
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

    with patch.object(
        SchedulingService,
        "list_specialties",
        wraps=scheduling.list_specialties,
    ) as list_specialties_mock, patch.object(
        SchedulingService,
        "list_doctors",
        wraps=scheduling.list_doctors,
    ) as list_doctors_mock:
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

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock, patch.object(
        SchedulingService,
        "get_available_slot_for_hold",
        wraps=scheduling.get_available_slot_for_hold,
    ) as hold_mock, patch.object(
        appointments,
        "add",
        wraps=appointments.add,
    ) as booking_mock:
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
    check_availability_mock.assert_not_called()


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

    with patch.object(
        SchedulingService,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock, patch.object(
        SchedulingService,
        "list_doctors",
        wraps=scheduling.list_doctors,
    ) as list_doctors_mock, patch.object(
        SchedulingService,
        "list_specialties",
        wraps=scheduling.list_specialties,
    ) as list_specialties_mock:
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


def test_availability_with_specialty_and_multiple_doctors_prompts_for_doctor_choice(
    scheduling_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = scheduling_chat_service
    dermatology = create_specialty(name="Dermatology")
    doctors = [
        Doctor(
            id=uuid4(),
            specialty_id=dermatology.id,
            full_name="Dr. Emily Carter",
            email="emily.carter@example-clinic.test",
            phone_number="+1-555-0101",
            is_active=True,
        ),
        Doctor(
            id=uuid4(),
            specialty_id=dermatology.id,
            full_name="Dr. James Lopez",
            email="james.lopez@example-clinic.test",
            phone_number="+1-555-0104",
            is_active=True,
        ),
    ]
    service_with_multiple_dermatologists = create_chat_receptionist_service(
        conversations=service.conversations,
        scheduling=create_service(
            specialties=[dermatology],
            doctors=doctors,
        ),
    )

    result = service_with_multiple_dermatologists.handle_message(
        ChatMessageInput(
            message="What availability does dermatology have on 2026-07-15?",
        ),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR
    assert "Dr. Emily Carter" in result.reply
    assert "Dr. James Lopez" in result.reply
    assert result.conversation.conversation_metadata["chat_context"]["selected_specialty_name"] == (
        "Dermatology"
    )


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
    assert "not booked yet" in result.reply.lower()
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
    assert (
        result.conversation.conversation_metadata["chat_context"]["selected_availability_slot_id"]
        == str(EMILY_JULY_SLOT_2_ID)
    )


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
    assert "temporarily held" in reply
    assert "not booked yet" in reply
    assert "full name" in reply
    assert "date of birth" in reply
    assert "phone" in reply
    assert "email" in reply


class SpyDeterministicChatResponder(DeterministicChatResponder):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    def generate_reply(self, *, message: str) -> ChatReceptionistReply:
        self.call_count += 1

        return super().generate_reply(message=message)
