from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.scheduling.expressions import DateExpressionKind, Weekday
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.schemas.retell_tools import CheckAvailabilityToolArguments, RetellToolCallRequest
from app.schemas.scheduling_expressions import DateExpressionSchema
from app.services.appointment_holds import AppointmentHoldService
from app.services.conversations import ConversationService
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling_availability import SchedulingAvailabilityResolver
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC, make_test_clinic_time_service
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_tool_adapter import (
    AdapterBundle,
    TrackingAppointmentHoldRepository,
    TrackingSchedulingService,
    TrackingVoiceCallRepository,
)
from tests.test_retell_voice_booking_tool import (
    _hold_id,
    _tool_request,
    create_retell_booking_tool_context,
)
from tests.test_scheduling_services import create_service


def _build_adapter(
    *,
    availability_slots: list[AvailabilitySlot] | None = None,
) -> tuple[RetellToolCallingAdapter, TrackingSchedulingService]:
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
    if availability_slots is None:
        start_time = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
        availability_slots = [
            AvailabilitySlot(
                id=uuid4(),
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=start_time + timedelta(minutes=30),
                status=AvailabilitySlotStatus.AVAILABLE,
            ),
        ]

    scheduling_service = TrackingSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        availability_slots=availability_slots,
    )
    hold_repository = TrackingAppointmentHoldRepository()
    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=AppointmentHoldService(repository=hold_repository, ttl_seconds=300),
        voice_calls=TrackingVoiceCallRepository(),
        clinic_time_service=make_test_clinic_time_service(),
    )
    return adapter, scheduling_service


def test_check_availability_next_weekday_resolves_correctly() -> None:
    adapter, scheduling_service = _build_adapter()

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-next-tuesday",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(scheduling_service.doctors[0].id),
                    "date_expression": {
                        "kind": "next_weekday",
                        "weekday": "tuesday",
                    },
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(scheduling_service.check_availability_calls) == 1
    call = scheduling_service.check_availability_calls[0]
    assert call.start_from.astimezone(UTC).date() == date(2026, 7, 7)


def test_check_availability_sunday_returns_clinic_closed() -> None:
    adapter, scheduling_service = _build_adapter()

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-sunday",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(scheduling_service.doctors[0].id),
                    "date_expression": {
                        "kind": "exact_date",
                        "exact_date": "2026-07-05",
                    },
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "clinic_closed"
    assert scheduling_service.check_availability_calls == []


def test_check_availability_midnight_exact_time_rejected() -> None:
    adapter, scheduling_service = _build_adapter()

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-midnight",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(scheduling_service.doctors[0].id),
                    "date_expression": {
                        "kind": "tomorrow",
                    },
                    "time_window_expression": {
                        "kind": "exact_time",
                        "exact_time": "00:00",
                    },
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "outside_business_hours"
    assert scheduling_service.check_availability_calls == []


def test_check_availability_past_date_rejected() -> None:
    adapter, scheduling_service = _build_adapter()

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-past",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(scheduling_service.doctors[0].id),
                    "date_expression": {
                        "kind": "exact_date",
                        "exact_date": "2026-06-01",
                    },
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "past_date"
    assert scheduling_service.check_availability_calls == []


def test_check_availability_valid_weekday_business_hours_works() -> None:
    adapter, scheduling_service = _build_adapter()

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-valid",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(scheduling_service.doctors[0].id),
                    "date_expression": {
                        "kind": "exact_date",
                        "exact_date": "2026-07-07",
                    },
                    "time_window_expression": {
                        "kind": "exact_time",
                        "exact_time": "10:00",
                    },
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(scheduling_service.check_availability_calls) == 1


def test_regression_retell_voice_hold_book_reschedule(adapter_bundle: AdapterBundle) -> None:
    availability_response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-availability",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                    "limit": 1,
                },
            },
        ),
    )
    assert availability_response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.check_availability_calls) == 1

    hold_response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-hold",
                "tool_call_id": "hold-regression-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(adapter_bundle.availability_slot.id),
                },
            },
        ),
    )
    assert hold_response.status == "succeeded"
    assert len(adapter_bundle.hold_repository.create_calls) == 1

    hold = AppointmentHold.create(
        availability_slot_id=adapter_bundle.availability_slot.id,
        doctor_id=adapter_bundle.availability_slot.doctor_id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        owner_id="retell-call-regression-release",
    )
    adapter_bundle.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    adapter_bundle.hold_repository.holds_by_id[hold.hold_id] = hold

    release_response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-release",
                "tool_call_id": "release-regression-1",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": str(hold.hold_id),
                },
            },
        ),
    )
    assert release_response.status == "succeeded"

    booking_context = create_retell_booking_tool_context()
    book_response = booking_context["adapter"].execute(
        _tool_request(
            hold_id=_hold_id(booking_context["booking_context"]),
            slot_id=str(booking_context["booking_context"].slot.id),
            tool_call_id="book-regression-1",
        ),
    )
    assert book_response.status == "succeeded"


def test_chat_availability_rejects_sunday() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=create_service(),
        clinic_time_service=make_test_clinic_time_service(),
        date_parser=NaturalLanguageDateParser(
            clock=FixedClock(current_date=REFERENCE_CLINIC_NOW_UTC.date()),
        ),
    )

    reply = service._handle_availability_flow(
        merged_context={
            "selected_doctor_id": str(uuid4()),
            "selected_doctor_name": "Dr. Emily Carter",
            "requested_date": "2026-07-05",
        },
        context_updates={},
    )

    assert reply.intent.value == "invalid_date"


def test_resolver_prefers_date_expression_over_requested_date_text() -> None:
    clinic_time = make_test_clinic_time_service()
    resolver = SchedulingAvailabilityResolver(clinic_time)

    window = resolver.resolve_check_availability(
        CheckAvailabilityToolArguments(
            date_expression=DateExpressionSchema(
                kind=DateExpressionKind.NEXT_WEEKDAY,
                weekday=Weekday.TUESDAY,
            ),
            requested_date_text="2026-06-01",
        ),
        {},
    )

    assert window.is_resolved
    assert window.resolved_date == date(2026, 7, 7)
