from __future__ import annotations

from datetime import date

from app.ai.receptionist_output import (
    ReceptionistExtractedFields,
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
)
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.slot_filling import LLMChatSlotFillingService, SlotFillingAppliedField
from app.services.time_preferences import TimePreferenceParser
from tests.test_scheduling_services import create_demo_scheduling_service

REFERENCE_DATE = date(2026, 7, 1)


def create_fixed_date_parser() -> NaturalLanguageDateParser:
    return NaturalLanguageDateParser(clock=FixedClock(current_date=REFERENCE_DATE))


def create_slot_filling_service(
    *,
    date_parser: NaturalLanguageDateParser | None = None,
) -> LLMChatSlotFillingService:
    return LLMChatSlotFillingService(
        scheduling=create_demo_scheduling_service(),
        date_parser=date_parser or create_fixed_date_parser(),
        time_preference_parser=TimePreferenceParser(),
    )


def create_scheduling_analysis(
    *,
    specialty: str | None = None,
    doctor_name: str | None = None,
    date: str | None = None,
    time: str | None = None,
) -> ReceptionistLLMAnalysis:
    return ReceptionistLLMAnalysis(
        intent=ReceptionistLLMIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        urgency=ReceptionistUrgency.NORMAL,
        extracted=ReceptionistExtractedFields(
            specialty=specialty,
            doctor_name=doctor_name,
            date=date,
            time=time,
        ),
    )


def test_specialty_validation_applies_dermatology() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(specialty="Dermatology")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    context = result.updated_chat_context
    assert context["selected_specialty_name"] == "Dermatology"
    assert context["selected_specialty_id"]
    assert any(field.field == "specialty" for field in result.applied_fields)
    assert result.rejected_fields == []


def test_unknown_specialty_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(specialty="Neurology")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "selected_specialty_id" not in result.updated_chat_context
    assert "selected_specialty_name" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "specialty"
    assert result.rejected_fields[0].reason == "unknown_specialty"


def test_doctor_validation_applies_emily_carter() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(doctor_name="Dr. Emily Carter")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    context = result.updated_chat_context
    assert context["selected_doctor_name"] == "Dr. Emily Carter"
    assert context["selected_doctor_id"]
    assert any(field.field == "doctor_name" for field in result.applied_fields)
    assert result.rejected_fields == []


def test_unknown_doctor_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(doctor_name="Dr. Unknown Person")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "selected_doctor_id" not in result.updated_chat_context
    assert "selected_doctor_name" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "doctor_name"
    assert result.rejected_fields[0].reason == "unknown_doctor"


def test_invalid_date_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(date="2026-99-99")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_date" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "date"
    assert result.rejected_fields[0].reason == "invalid_date"


def test_time_validation_normalizes_to_hh_mm() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="9:00")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert result.updated_chat_context["requested_time"] == "09:00"
    assert any(field.field == "time" for field in result.applied_fields)
    assert result.rejected_fields == []


def test_specialty_context_conflict_is_rejected() -> None:
    scheduling = create_demo_scheduling_service()
    service = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=create_fixed_date_parser(),
        time_preference_parser=TimePreferenceParser(),
    )
    cardiology = next(
        specialty for specialty in scheduling.list_specialties() if specialty.name == "Cardiology"
    )
    chat_context = {
        "selected_specialty_id": str(cardiology.id),
        "selected_specialty_name": "Cardiology",
    }
    analysis = create_scheduling_analysis(specialty="Dermatology")

    result = service.apply_analysis(analysis=analysis, chat_context=chat_context)

    assert result.updated_chat_context["selected_specialty_name"] == "Cardiology"
    assert result.updated_chat_context["selected_specialty_id"] == str(cardiology.id)
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "specialty"
    assert result.rejected_fields[0].reason == "conflicts_with_existing_context"


def test_natural_language_date_applies_normalized_requested_date() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(date="tomorrow")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert result.updated_chat_context["requested_date"] == "2026-07-02"
    assert result.applied_fields == [
        SlotFillingAppliedField(field="date", value="2026-07-02"),
    ]
    assert result.date_parsing == {
        "status": "parsed",
        "normalized_date": "2026-07-02",
        "source_text": "tomorrow",
        "reason": None,
    }
    assert result.to_metadata()["date_parsing"] == result.date_parsing


def test_unsupported_date_expression_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(date="next week")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_date" not in result.updated_chat_context
    assert result.rejected_fields[0].field == "date"
    assert result.rejected_fields[0].reason == "unsupported_date_expression"
    assert result.date_parsing is not None
    assert result.date_parsing["status"] == "unsupported"


def test_existing_requested_date_conflict_rejects_new_parsed_date() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(date="tomorrow")

    result = service.apply_analysis(
        analysis=analysis,
        chat_context={"requested_date": "2026-07-03"},
    )

    assert result.updated_chat_context["requested_date"] == "2026-07-03"
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "date"
    assert result.rejected_fields[0].reason == "conflicts_with_existing_context"
    assert result.date_parsing is not None
    assert result.date_parsing["status"] == "parsed"


def test_iso_date_still_applies_requested_date() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(date="2026-07-02")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert result.updated_chat_context["requested_date"] == "2026-07-02"
    assert result.applied_fields == [
        SlotFillingAppliedField(field="date", value="2026-07-02"),
    ]
    assert result.rejected_fields == []
    assert result.date_parsing == {
        "status": "parsed",
        "normalized_date": "2026-07-02",
        "source_text": "2026-07-02",
        "reason": None,
    }


def test_time_preference_applies_requested_time_window() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="morning")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert result.updated_chat_context["requested_time_window"] == {
        "label": "morning",
        "start_time": "08:00",
        "end_time": "12:00",
    }
    assert result.applied_fields == [
        SlotFillingAppliedField(field="time_preference", value="morning"),
    ]
    assert result.rejected_fields == []
    assert result.time_preference_parsing == {
        "status": "parsed",
        "label": "morning",
        "start_time": "08:00",
        "end_time": "12:00",
        "source_text": "morning",
        "reason": None,
    }
    assert result.to_metadata()["time_preference_parsing"] == result.time_preference_parsing


def test_after_lunch_time_preference_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="after lunch")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_time_window" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time_preference"
    assert result.rejected_fields[0].reason == "unsupported_time_preference"
    assert result.time_preference_parsing is not None
    assert result.time_preference_parsing["status"] == "unsupported"


def test_unsupported_time_preference_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="early morning")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_time_window" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time_preference"
    assert result.rejected_fields[0].reason == "unsupported_time_preference"
    assert result.time_preference_parsing is not None
    assert result.time_preference_parsing["status"] == "unsupported"


def test_ambiguous_time_preference_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="morning or afternoon")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_time_window" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time_preference"
    assert result.rejected_fields[0].reason == "ambiguous_time_preference"
    assert result.time_preference_parsing is not None
    assert result.time_preference_parsing["status"] == "ambiguous"


def test_unrecognized_time_keeps_invalid_time_rejection() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="noon")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "requested_time_window" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time"
    assert result.rejected_fields[0].reason == "invalid_time"
    assert result.time_preference_parsing is not None
    assert result.time_preference_parsing["status"] == "not_found"


def test_existing_requested_time_window_conflict_rejects_new_preference() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="morning")

    result = service.apply_analysis(
        analysis=analysis,
        chat_context={
            "requested_time_window": {
                "label": "evening",
                "start_time": "17:00",
                "end_time": "20:00",
            },
        },
    )

    assert result.updated_chat_context["requested_time_window"] == {
        "label": "evening",
        "start_time": "17:00",
        "end_time": "20:00",
    }
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time_preference"
    assert result.rejected_fields[0].reason == "conflicts_with_existing_context"


def test_existing_requested_time_blocks_time_preference_application() -> None:
    service = create_slot_filling_service()
    analysis = create_scheduling_analysis(time="morning")

    result = service.apply_analysis(
        analysis=analysis,
        chat_context={"requested_time": "09:00"},
    )

    assert result.updated_chat_context["requested_time"] == "09:00"
    assert "requested_time_window" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "time_preference"
    assert result.rejected_fields[0].reason == "conflicts_with_existing_context"
