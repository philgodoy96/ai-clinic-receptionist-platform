from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domain.conversations.enums import ConversationChannel
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient
from app.services.chat_appointment_rescheduling import _ReschedulePreferenceExtraction
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.clinic_time import (
    format_clinic_local_slot_summary,
    format_clinic_local_time_label,
    to_clinic_local_datetime,
)
from app.services.conversations import ConversationCreate, ConversationService
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.scheduling import SchedulingService
from app.services.time_preferences import TimePreferenceParser
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_chat_appointment_cancellation import (
    _add_appointments,
    _create_chat_service_with_patient,
    _felipe_patient,
    _wednesday_appointment,
)
from tests.test_chat_appointment_rescheduling import _reach_reschedule_new_time_preference
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    _create_availability_guidance_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAvailabilitySlotRepository,
    create_availability_slot,
    create_service,
    create_specialty,
)


def _availability_slots(scheduling: SchedulingService) -> list[AvailabilitySlot]:
    repository = scheduling.availability_slots
    if isinstance(repository, FakeAvailabilitySlotRepository):
        return list(repository.slots)
    raise AssertionError("expected fake availability slot repository in tests")


NEW_YORK = ZoneInfo("America/New_York")
SUMMER_10_ET_UTC = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
SUMMER_13_ET_UTC = datetime(2026, 7, 2, 17, 0, tzinfo=UTC)
SUMMER_06_ET_UTC = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)


def test_to_clinic_local_datetime_treats_naive_values_as_utc() -> None:
    naive = datetime(2026, 7, 2, 14, 0)
    localized = to_clinic_local_datetime(naive, NEW_YORK)
    assert localized == datetime(2026, 7, 2, 10, 0, tzinfo=NEW_YORK)


def test_format_clinic_local_time_label_converts_utc_to_clinic_local() -> None:
    assert format_clinic_local_time_label(SUMMER_10_ET_UTC, NEW_YORK) == "10:00"
    assert format_clinic_local_time_label(SUMMER_13_ET_UTC, NEW_YORK) == "13:00"


def test_format_clinic_local_slot_summary_uses_weekday_and_time() -> None:
    assert (
        format_clinic_local_slot_summary(SUMMER_10_ET_UTC, NEW_YORK)
        == "Thursday at 10:00"
    )


def _create_emily_with_summer_slots() -> tuple[SchedulingService, Doctor]:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=SUMMER_10_ET_UTC,
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=emily.id,
            start_time=SUMMER_13_ET_UTC,
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        availability_slots=slots,
    )
    return scheduling, emily


def _create_reschedule_service(
    *,
    scheduling: SchedulingService,
    patient: Patient,
) -> tuple[ChatReceptionistService, Patient]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=FakeAppointmentHoldService(),
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )
    return service, patient


def test_booking_availability_display_renders_clinic_local_time() -> None:
    scheduling, _emily = _create_emily_with_summer_slots()
    service, _repository, _holds = _create_availability_guidance_service(scheduling)

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "10:00" in result.reply
    assert "13:00" in result.reply
    assert "14:00" not in result.reply
    assert "17:00" not in result.reply


def test_booking_offered_slot_display_time_uses_clinic_local_time() -> None:
    scheduling, _emily = _create_emily_with_summer_slots()
    service, _repository, _holds = _create_availability_guidance_service(scheduling)

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )

    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    display_times = {slot["display_time"] for slot in offered_slots}
    assert display_times == {"10:00", "13:00"}
    assert all(
        slot["start_time"] in {"2026-07-02T14:00:00+00:00", "2026-07-02T17:00:00+00:00"}
        for slot in offered_slots
    )


def test_booking_afternoon_filter_uses_clinic_local_time() -> None:
    scheduling, _emily = _create_emily_with_summer_slots()
    service, _repository, _holds = _create_availability_guidance_service(scheduling)

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter afternoon on 2026-07-02"),
    )

    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    assert len(offered_slots) == 1
    assert offered_slots[0]["display_time"] == "13:00"
    assert offered_slots[0]["start_time"] == "2026-07-02T17:00:00+00:00"
    assert "10:00" not in result.reply


def _recreate_scheduling_with_patient(
    base: SchedulingService,
    *,
    emily: Doctor,
    patient: Patient,
    availability_slots: list[AvailabilitySlot],
) -> SchedulingService:
    specialty = base.specialties.get_by_id(emily.specialty_id)
    assert specialty is not None
    return create_service(
        specialties=[specialty],
        doctors=[emily],
        patients=[patient],
        availability_slots=availability_slots,
    )


def test_reschedule_exact_time_filter_uses_clinic_local_time() -> None:
    scheduling, emily = _create_emily_with_summer_slots()
    patient = _felipe_patient()
    scheduling = _recreate_scheduling_with_patient(
        scheduling,
        emily=emily,
        patient=patient,
        availability_slots=list(_availability_slots(scheduling)),
    )
    service, patient = _create_reschedule_service(scheduling=scheduling, patient=patient)
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message="Thursday at 10:00", conversation_id=conversation_id),
    )

    offered = result.conversation.conversation_metadata["chat_context"]["reschedule_offered_slots"]
    assert len(offered) == 1
    assert offered[0]["start_time"] == "2026-07-02T14:00:00+00:00"
    assert "10:00" in offered[0]["summary"]


def test_reschedule_afternoon_filter_uses_clinic_local_time() -> None:
    scheduling, emily = _create_emily_with_summer_slots()
    patient = _felipe_patient()
    scheduling = _recreate_scheduling_with_patient(
        scheduling,
        emily=emily,
        patient=patient,
        availability_slots=list(_availability_slots(scheduling)),
    )
    service, patient = _create_reschedule_service(scheduling=scheduling, patient=patient)
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message="Thursday afternoon", conversation_id=conversation_id),
    )

    offered = result.conversation.conversation_metadata["chat_context"]["reschedule_offered_slots"]
    assert len(offered) == 1
    assert offered[0]["start_time"] == "2026-07-02T17:00:00+00:00"
    assert "13:00" in offered[0]["summary"]
    assert "10:00" not in result.reply


def test_reschedule_display_summary_renders_clinic_local_time() -> None:
    scheduling, emily = _create_emily_with_summer_slots()
    service, _patient = _create_reschedule_service(
        scheduling=scheduling,
        patient=_felipe_patient(),
    )
    offered = service._appointment_rescheduling._serialize_reschedule_offered_slots(
        list(_availability_slots(scheduling))[:1],
        specialty_id=None,
        specialty_name=None,
        default_doctor_name="Dr. Emily Carter",
    )
    assert offered[0]["summary"] == "Thursday at 10:00"
    assert offered[0]["start_time"] == "2026-07-02T14:00:00+00:00"


def test_reschedule_exact_time_does_not_match_utc_wall_clock() -> None:
    scheduling, emily = _create_emily_with_summer_slots()
    morning_utc_slot = create_availability_slot(
        doctor_id=emily.id,
        start_time=SUMMER_06_ET_UTC,
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    scheduling = _recreate_scheduling_with_patient(
        scheduling,
        emily=emily,
        patient=_felipe_patient(),
        availability_slots=[morning_utc_slot],
    )
    service, _patient = _create_reschedule_service(
        scheduling=scheduling,
        patient=_felipe_patient(),
    )
    filtered = service._appointment_rescheduling._filter_doctor_slots_by_preference(
        list(_availability_slots(scheduling)),
        preference=_ReschedulePreferenceExtraction(exact_time="10:00"),
    )
    assert filtered == []


# 2026-07-06 is a Monday, 2026-07-07 a Tuesday; clinic is America/New_York (EDT, UTC-4).
EARLIEST_MONDAY_10_ET_UTC = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)
EARLIEST_MONDAY_11_ET_UTC = datetime(2026, 7, 6, 15, 0, tzinfo=UTC)
EARLIEST_MONDAY_14_ET_UTC = datetime(2026, 7, 6, 18, 0, tzinfo=UTC)
EARLIEST_MONDAY_15_ET_UTC = datetime(2026, 7, 6, 19, 0, tzinfo=UTC)
EARLIEST_TUESDAY_10_ET_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)


def _create_emily_multiday_chat_service() -> tuple[
    ChatReceptionistService,
    Doctor,
    list[AvailabilitySlot],
]:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=start_time,
            status=AvailabilitySlotStatus.AVAILABLE,
        )
        for start_time in (
            EARLIEST_MONDAY_10_ET_UTC,
            EARLIEST_MONDAY_11_ET_UTC,
            EARLIEST_MONDAY_14_ET_UTC,
            EARLIEST_MONDAY_15_ET_UTC,
            EARLIEST_TUESDAY_10_ET_UTC,
        )
    ]
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        availability_slots=slots,
        clinic_time_service=make_test_clinic_time_service(),
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
        clinic_time_service=make_test_clinic_time_service(),
    )
    return service, emily, slots


def test_format_earliest_doctor_availability_groups_by_clinic_local_date() -> None:
    service, _emily, slots = _create_emily_multiday_chat_service()

    message = service._format_earliest_doctor_availability_slots(
        slots,
        doctor_name="Dr. Emily Carter",
    )

    assert message == (
        "I found openings with Dr. Emily Carter "
        "Monday at 10:00, 11:00, 14:00, and 15:00; Tuesday at 10:00. "
        "Which time works better?"
    )


def test_format_earliest_doctor_availability_does_not_collapse_dates() -> None:
    service, _emily, slots = _create_emily_multiday_chat_service()

    message = service._format_earliest_doctor_availability_slots(
        slots,
        doctor_name="Dr. Emily Carter",
    )

    # The Tuesday 10:00 must stay under the Tuesday label, not collapse into Monday.
    assert "; Tuesday at 10:00" in message
    assert "15:00, and 10:00" not in message
    assert message.count("10:00") == 2


def test_earliest_doctor_offered_slots_store_per_slot_clinic_local_date() -> None:
    service, emily, slots = _create_emily_multiday_chat_service()

    offered = service._serialize_offered_slots(
        slots,
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )

    assert [slot["display_date"] for slot in offered] == [
        "2026-07-06",
        "2026-07-06",
        "2026-07-06",
        "2026-07-06",
        "2026-07-07",
    ]
    tuesday = offered[-1]
    assert tuesday["display_time"] == "10:00"
    assert tuesday["display_date"] == "2026-07-07"


def test_earliest_doctor_offered_slot_display_date_uses_clinic_local_not_utc() -> None:
    service, emily, _slots = _create_emily_multiday_chat_service()
    # 2026-07-07 02:00 UTC is 2026-07-06 22:00 in clinic-local (EDT) time.
    boundary_slot = create_availability_slot(
        doctor_id=emily.id,
        start_time=datetime(2026, 7, 7, 2, 0, tzinfo=UTC),
        status=AvailabilitySlotStatus.AVAILABLE,
    )

    offered = service._serialize_offered_slots(
        [boundary_slot],
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )

    assert offered[0]["display_date"] == "2026-07-06"
    assert offered[0]["display_time"] == "22:00"
    assert offered[0]["start_time"] == "2026-07-07T02:00:00+00:00"


def test_select_offered_slot_duplicate_display_time_is_ambiguous() -> None:
    service, emily, slots = _create_emily_multiday_chat_service()
    offered = service._serialize_offered_slots(
        slots,
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )

    selection = service._select_offered_slot("10:00", "10:00", offered)

    assert selection.ambiguous is True
    assert selection.slot is None


def test_select_offered_slot_option_number_is_deterministic_with_duplicates() -> None:
    service, emily, slots = _create_emily_multiday_chat_service()
    offered = service._serialize_offered_slots(
        slots,
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )

    selection = service._select_offered_slot("the first one", "the first one", offered)

    assert selection.ambiguous is False
    assert selection.slot is offered[0]


def test_select_offered_slot_full_iso_disambiguates_duplicate_display_time() -> None:
    service, emily, slots = _create_emily_multiday_chat_service()
    offered = service._serialize_offered_slots(
        slots,
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )

    selection = service._select_offered_slot(
        "2026-07-07T14:00:00+00:00",
        "2026-07-07t14:00:00+00:00",
        offered,
    )

    assert selection.ambiguous is False
    assert selection.slot is not None
    assert selection.slot["start_time"] == "2026-07-07T14:00:00+00:00"


def test_earliest_doctor_flow_groups_dates_and_stores_clinic_local_dates() -> None:
    service, emily, _slots = _create_emily_multiday_chat_service()

    reply = service._handle_earliest_doctor_availability_flow(
        merged_context={
            "selected_doctor_id": str(emily.id),
            "selected_doctor_name": "Dr. Emily Carter",
        },
        context_updates={},
        start_date=date(2026, 7, 6),
        end_date=date(2026, 7, 7),
    )

    assert reply.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert (
        "Monday at 10:00, 11:00, 14:00, and 15:00; Tuesday at 10:00" in reply.content
    )
    offered = reply.chat_context_updates["offered_slots"]
    assert {slot["display_date"] for slot in offered} == {"2026-07-06", "2026-07-07"}


def test_hold_flow_reprompts_when_bare_time_is_ambiguous() -> None:
    service, emily, slots = _create_emily_multiday_chat_service()
    offered = service._serialize_offered_slots(
        slots,
        doctor_names={emily.id: "Dr. Emily Carter"},
        use_slot_date=True,
    )
    conversation = service.conversations.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    reply = service._handle_hold_flow(
        message="10:00",
        normalized_message="10:00",
        conversation=conversation,
        merged_context={"offered_slots": offered},
        context_updates={},
    )

    assert reply.intent == ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND
    assert reply.content == (
        "I found more than one matching time. Please choose by option number or day."
    )
    assert reply.hold_created is False


def test_cancellation_summary_renders_clinic_local_time() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
        start_time=SUMMER_10_ET_UTC,
        end_time=datetime(2026, 7, 2, 14, 30, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
        reason="Dermatology follow-up",
    )
    _add_appointments(service, [appointment])
    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    reply = result.reply
    assert "10:00" in reply
    assert "14:00" not in reply
