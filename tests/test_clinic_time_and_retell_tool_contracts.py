"""Comprehensive contract tests for clinic time context and Retell scheduling tools."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_retell_signature_verifier, get_retell_tool_calling_adapter
from app.core.config import Settings, get_settings
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    DateResolutionStatus,
    TimeWindowExpression,
    TimeWindowExpressionKind,
    TimeWindowResolutionStatus,
    Weekday,
)
from app.integrations.retell.signature import HmacRetellSignatureVerifier
from app.main import create_app
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.schemas.retell_tools import CheckAvailabilityToolArguments, RetellToolCallRequest
from app.schemas.scheduling_expressions import DateExpressionSchema, TimeWindowExpressionSchema
from app.services.appointment_holds import AppointmentHoldService
from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock
from app.services.conversations import ConversationService
from app.services.date_parsing import FixedClock as DateParserClock
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling_availability import SchedulingAvailabilityResolver
from tests.clinic_time_test_support import (
    CHECK_AVAILABILITY_LEGACY_END,
    CHECK_AVAILABILITY_LEGACY_START,
    REFERENCE_CLINIC_NOW_UTC,
    make_test_clinic_time_service,
)
from tests.retell_cancellation_test_support import (
    cancellation_tool_request,
    create_retell_cancellation_tool_context,
)
from tests.retell_rescheduling_test_support import (
    create_retell_rescheduling_tool_context,
    reschedule_tool_request,
)
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    configure_retell_for_tests,
    make_secured_retell_settings,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_clinic_time_config import load_settings
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_clinic_context_tool import make_clinic_context_adapter
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

WEBHOOK_SECRET = "test-webhook-secret"
TIMESTAMP_MS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1. Config defaults and invalid config
# ---------------------------------------------------------------------------


def test_config_defaults_match_clinic_time_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CLINIC_TIMEZONE",
        "CLINIC_BUSINESS_DAYS",
        "CLINIC_BUSINESS_HOURS_START",
        "CLINIC_BUSINESS_HOURS_END",
        "CLINIC_NAME",
        "CLINIC_LOCALE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings(monkeypatch)

    assert settings.clinic_timezone == "America/New_York"
    assert settings.clinic_business_days == "monday,tuesday,wednesday,thursday,friday"
    assert settings.clinic_business_hours_start == "09:00"
    assert settings.clinic_business_hours_end == "17:00"


@pytest.mark.parametrize(
    ("env_key", "env_value", "match"),
    [
        ("CLINIC_TIMEZONE", "Not/A_Real_Zone", "CLINIC_TIMEZONE"),
        ("CLINIC_BUSINESS_DAYS", "monday,funday", "CLINIC_BUSINESS_DAYS"),
        ("CLINIC_BUSINESS_HOURS_START", "9:00am", "HH:MM"),
        (
            "CLINIC_BUSINESS_HOURS_START",
            "17:00",
            "CLINIC_BUSINESS_HOURS_START",
        ),
    ],
    ids=[
        "invalid_timezone",
        "invalid_weekday",
        "invalid_time_format",
        "start_after_end",
    ],
)
def test_invalid_clinic_config_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_value: str,
    match: str,
) -> None:
    env: dict[str, str] = {env_key: env_value}
    if env_key == "CLINIC_BUSINESS_HOURS_START" and env_value == "17:00":
        env["CLINIC_BUSINESS_HOURS_END"] = "09:00"

    with pytest.raises(ValidationError, match=match):
        load_settings(monkeypatch, **env)


def test_clinic_time_service_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        CLINIC_NAME="Contract Clinic",
        CLINIC_TIMEZONE="America/Chicago",
    )
    service = ClinicTimeService.from_settings(
        settings,
        clock=FixedClock(current_time=REFERENCE_CLINIC_NOW_UTC),
    )

    assert service.get_current_clinic_context().clinic_name == "Contract Clinic"
    assert service.get_current_clinic_context().clinic_timezone == "America/Chicago"


# ---------------------------------------------------------------------------
# 2. DateExpression validation
# ---------------------------------------------------------------------------


def test_date_expression_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DateExpressionSchema.model_validate(
            {
                "kind": "today",
                "unexpected": "value",
            },
        )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"kind": "next_weekday"},
            "weekday is required",
        ),
        (
            {"kind": "this_weekday"},
            "weekday is required",
        ),
        (
            {"kind": "in_n_days"},
            "days_offset is required",
        ),
        (
            {"kind": "exact_date"},
            "exact_date is required",
        ),
    ],
)
def test_check_availability_rejects_incomplete_date_expressions(
    payload: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        CheckAvailabilityToolArguments.model_validate(
            {
                "doctor_id": str(uuid4()),
                "date_expression": payload,
            },
        )


def test_date_expression_schema_rejects_negative_days_offset() -> None:
    with pytest.raises(ValidationError):
        DateExpressionSchema.model_validate(
            {
                "kind": "in_n_days",
                "days_offset": -1,
            },
        )


def test_valid_date_expression_schema_round_trips_to_domain() -> None:
    schema = DateExpressionSchema(
        kind=DateExpressionKind.NEXT_WEEKDAY,
        weekday=Weekday.FRIDAY,
    )

    domain = schema.to_domain()

    assert domain.kind is DateExpressionKind.NEXT_WEEKDAY
    assert domain.weekday is Weekday.FRIDAY


# ---------------------------------------------------------------------------
# 3. TimeWindowExpression validation
# ---------------------------------------------------------------------------


def test_time_window_expression_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TimeWindowExpressionSchema.model_validate(
            {
                "kind": "morning",
                "unexpected": "value",
            },
        )


def test_check_availability_rejects_exact_time_without_time_label() -> None:
    with pytest.raises(ValidationError, match="exact_time is required"):
        CheckAvailabilityToolArguments.model_validate(
            {
                "doctor_id": str(uuid4()),
                "date_expression": {"kind": "tomorrow"},
                "time_window_expression": {"kind": "exact_time"},
            },
        )


def test_valid_time_window_expression_schema_round_trips_to_domain() -> None:
    schema = TimeWindowExpressionSchema(
        kind=TimeWindowExpressionKind.AFTERNOON,
    )

    domain = schema.to_domain()

    assert domain.kind is TimeWindowExpressionKind.AFTERNOON
    assert domain.exact_time is None


# ---------------------------------------------------------------------------
# 4. ClinicTimeService timezone conversion
# ---------------------------------------------------------------------------


def test_clinic_time_service_converts_utc_clock_to_clinic_timezone() -> None:
    service = ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(current_time=datetime(2026, 7, 2, 3, 0, tzinfo=UTC)),
    )

    assert service.clinic_today() == date(2026, 7, 1)
    clinic_now = service.clinic_now()
    assert clinic_now.tzinfo is not None
    assert clinic_now.date() == date(2026, 7, 1)
    assert clinic_now.hour == 23


# ---------------------------------------------------------------------------
# 5. Date expression resolution (today / tomorrow / weekdays / exact / offset)
# ---------------------------------------------------------------------------


@pytest.fixture()
def clinic_time_service() -> ClinicTimeService:
    return make_test_clinic_time_service()


@pytest.mark.parametrize(
    ("expression", "expected_date"),
    [
        (DateExpression(kind=DateExpressionKind.TODAY), date(2026, 7, 1)),
        (DateExpression(kind=DateExpressionKind.TOMORROW), date(2026, 7, 2)),
        (
            DateExpression(
                kind=DateExpressionKind.THIS_WEEKDAY,
                weekday=Weekday.FRIDAY,
            ),
            date(2026, 7, 3),
        ),
        (
            DateExpression(
                kind=DateExpressionKind.NEXT_WEEKDAY,
                weekday=Weekday.TUESDAY,
            ),
            date(2026, 7, 7),
        ),
        (
            DateExpression(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=date(2026, 7, 10),
            ),
            date(2026, 7, 10),
        ),
        (
            DateExpression(
                kind=DateExpressionKind.IN_N_DAYS,
                days_offset=2,
            ),
            date(2026, 7, 3),
        ),
        (DateExpression(kind=DateExpressionKind.NEXT_WEEK), date(2026, 7, 6)),
    ],
)
def test_clinic_time_service_resolves_date_expressions(
    clinic_time_service: ClinicTimeService,
    expression: DateExpression,
    expected_date: date,
) -> None:
    result = clinic_time_service.resolve_date(expression)

    assert result.status is DateResolutionStatus.RESOLVED
    assert result.resolved_date == expected_date


# ---------------------------------------------------------------------------
# 6. Unknown / ambiguous expression behavior
# ---------------------------------------------------------------------------


def test_unknown_date_expression_returns_unknown_status(
    clinic_time_service: ClinicTimeService,
) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(kind=DateExpressionKind.UNKNOWN),
    )

    assert result.status is DateResolutionStatus.UNKNOWN
    assert result.reason == "unknown_expression"


def test_weekday_expression_without_weekday_is_invalid_at_service_level(
    clinic_time_service: ClinicTimeService,
) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(kind=DateExpressionKind.NEXT_WEEKDAY),
    )

    assert result.status is DateResolutionStatus.UNKNOWN
    assert result.reason == "invalid_expression"


def test_unknown_time_window_expression_returns_unknown_status(
    clinic_time_service: ClinicTimeService,
) -> None:
    result = clinic_time_service.resolve_time_window(
        TimeWindowExpression(kind=TimeWindowExpressionKind.UNKNOWN),
    )

    assert result.status is TimeWindowResolutionStatus.UNKNOWN
    assert result.reason == "unknown_expression"


def test_invalid_exact_time_format_returns_unknown_status(
    clinic_time_service: ClinicTimeService,
) -> None:
    result = clinic_time_service.resolve_time_window(
        TimeWindowExpression(
            kind=TimeWindowExpressionKind.EXACT_TIME,
            exact_time="9am",
        ),
    )

    assert result.status is TimeWindowResolutionStatus.UNKNOWN
    assert result.reason == "invalid_exact_time"


def test_check_availability_unknown_date_expression_fails_at_adapter() -> None:
    adapter, scheduling_service = _make_check_availability_adapter()
    doctor_id = scheduling_service.doctors[0].id

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-unknown-date",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(doctor_id),
                    "date_expression": {"kind": "unknown"},
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "invalid_scheduling_expression"


# ---------------------------------------------------------------------------
# 7. Business days
# ---------------------------------------------------------------------------


def test_saturday_is_closed_day(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 7, 4),
        ),
    )

    assert result.status is DateResolutionStatus.CLOSED_DAY
    assert result.reason == "closed_day"


def test_clinic_context_lists_configured_business_days_only() -> None:
    adapter = make_clinic_context_adapter()
    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-business-days",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    assert response.result["business_days"] == [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
    ]


def test_custom_business_days_exclude_tuesday() -> None:
    service = ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,wednesday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(current_time=REFERENCE_CLINIC_NOW_UTC),
    )

    tuesday = service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 7, 7),
        ),
    )
    wednesday = service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 7, 8),
        ),
    )

    assert tuesday.status is DateResolutionStatus.CLOSED_DAY
    assert wednesday.status is DateResolutionStatus.RESOLVED


# ---------------------------------------------------------------------------
# 8. Business hours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("time_label", "expected"),
    [
        ("09:00", True),
        ("12:30", True),
        ("16:59", True),
        ("17:00", False),
        ("08:59", False),
    ],
)
def test_is_within_business_hours_boundaries(
    clinic_time_service: ClinicTimeService,
    time_label: str,
    expected: bool,
) -> None:
    assert clinic_time_service.is_within_business_hours(time_label) is expected


def test_standard_time_windows_resolve(clinic_time_service: ClinicTimeService) -> None:
    morning = clinic_time_service.resolve_time_window(
        TimeWindowExpression(kind=TimeWindowExpressionKind.MORNING),
    )
    afternoon = clinic_time_service.resolve_time_window(
        TimeWindowExpression(kind=TimeWindowExpressionKind.AFTERNOON),
    )

    assert morning.status is TimeWindowResolutionStatus.RESOLVED
    assert morning.start_time == "08:00"
    assert morning.end_time == "12:00"
    assert afternoon.status is TimeWindowResolutionStatus.RESOLVED
    assert afternoon.start_time == "12:00"
    assert afternoon.end_time == "17:00"


# ---------------------------------------------------------------------------
# 9. get_clinic_context tool
# ---------------------------------------------------------------------------


def test_get_clinic_context_tool_returns_clinic_contract_fields() -> None:
    adapter = make_clinic_context_adapter()
    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-context-contract",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    assert response.status == "succeeded"
    assert set(response.result) == {
        "clinic_name",
        "clinic_timezone",
        "current_date",
        "current_weekday",
        "business_days",
        "business_hours",
    }
    assert response.result["business_hours"] == {"start": "09:00", "end": "17:00"}


# ---------------------------------------------------------------------------
# 10–12. check_availability structured expressions, closed day, outside hours
# ---------------------------------------------------------------------------


def _make_check_availability_adapter() -> tuple[
    RetellToolCallingAdapter,
    TrackingSchedulingService,
]:
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
    start_time = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
    availability_slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    scheduling_service = TrackingSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        availability_slots=[availability_slot],
    )
    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=AppointmentHoldService(
            repository=TrackingAppointmentHoldRepository(),
            ttl_seconds=300,
        ),
        voice_calls=TrackingVoiceCallRepository(),
        clinic_time_service=make_test_clinic_time_service(),
    )
    return adapter, scheduling_service


def test_check_availability_structured_date_expression_queries_scheduling() -> None:
    adapter, scheduling_service = _make_check_availability_adapter()
    doctor_id = scheduling_service.doctors[0].id

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-structured-date",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(doctor_id),
                    "date_expression": {
                        "kind": "next_weekday",
                        "weekday": "tuesday",
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


def test_check_availability_closed_day_returns_clinic_closed_without_query() -> None:
    adapter, scheduling_service = _make_check_availability_adapter()
    doctor_id = scheduling_service.doctors[0].id

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-closed-day",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(doctor_id),
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


def test_check_availability_outside_business_hours_is_rejected() -> None:
    adapter, scheduling_service = _make_check_availability_adapter()
    doctor_id = scheduling_service.doctors[0].id

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-outside-hours",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(doctor_id),
                    "date_expression": {"kind": "tomorrow"},
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


def test_resolver_prefers_structured_date_over_legacy_text() -> None:
    resolver = SchedulingAvailabilityResolver(make_test_clinic_time_service())

    window = resolver.resolve_check_availability(
        CheckAvailabilityToolArguments(
            date_expression=DateExpressionSchema(
                kind=DateExpressionKind.TOMORROW,
            ),
            requested_date_text="2026-06-01",
        ),
        {},
    )

    assert window.is_resolved
    assert window.resolved_date == date(2026, 7, 2)


# ---------------------------------------------------------------------------
# 13. Invalid Retell signature blocks tool execution
# ---------------------------------------------------------------------------


class _SideEffectTrackingAdapter:
    def __init__(self, adapter: RetellToolCallingAdapter) -> None:
        self.adapter = adapter
        self.invocation_count = 0

    def execute(self, request: RetellToolCallRequest) -> Any:
        self.invocation_count += 1
        return self.adapter.execute(request)


def _check_availability_body(*, doctor_id: str) -> bytes:
    return (
        f'{{"provider_call_id":"retell-call-signature",'
        f'"tool_name":"check_availability",'
        f'"arguments":{{"doctor_id":"{doctor_id}",'
        f'"start_from":"{CHECK_AVAILABILITY_LEGACY_START}",'
        f'"start_to":"{CHECK_AVAILABILITY_LEGACY_END}"}}}}'
    ).encode()


def test_invalid_signature_blocks_check_availability_execution() -> None:
    adapter, scheduling_service = _make_check_availability_adapter()
    doctor_id = str(scheduling_service.doctors[0].id)
    app = create_app()
    settings = make_secured_retell_settings(RETELL_WEBHOOK_SECRET=WEBHOOK_SECRET)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = _SideEffectTrackingAdapter(adapter)
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=_check_availability_body(doctor_id=doctor_id),
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.invocation_count == 0
    assert scheduling_service.check_availability_calls == []


def test_valid_signature_allows_check_availability_execution() -> None:
    adapter, _scheduling_service = _make_check_availability_adapter()
    doctor_id = str(_scheduling_service.doctors[0].id)
    app = create_app()
    settings = make_secured_retell_settings(RETELL_WEBHOOK_SECRET=WEBHOOK_SECRET)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = _SideEffectTrackingAdapter(adapter)
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )
    raw_body = _check_availability_body(doctor_id=doctor_id)
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert tracking_adapter.invocation_count == 1


# ---------------------------------------------------------------------------
# 14. Existing chat scheduling regression
# ---------------------------------------------------------------------------


def test_chat_scheduling_rejects_closed_day_with_clinic_time_service() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=create_service(),
        clinic_time_service=make_test_clinic_time_service(),
        date_parser=NaturalLanguageDateParser(
            clock=DateParserClock(current_date=REFERENCE_CLINIC_NOW_UTC.date()),
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


def test_chat_scheduling_accepts_valid_weekday_with_clinic_time_service() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=create_service(),
        clinic_time_service=make_test_clinic_time_service(),
        date_parser=NaturalLanguageDateParser(
            clock=DateParserClock(current_date=REFERENCE_CLINIC_NOW_UTC.date()),
        ),
    )

    validation_reply = service._validate_scheduling_date(date(2026, 7, 7))

    assert validation_reply is None


# ---------------------------------------------------------------------------
# 15. Existing Retell booking / cancel / reschedule regression
# ---------------------------------------------------------------------------


def test_regression_retell_booking_tool_still_works() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]

    response = context["adapter"].execute(
        _tool_request(
            hold_id=_hold_id(booking_context),
            slot_id=str(booking_context.slot.id),
        ),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1


def test_regression_retell_cancellation_tool_still_works() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        cancellation_tool_request(appointment_id=str(appointment.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_cancellation"].cancel_calls) == 1


def test_regression_retell_reschedule_tool_still_works() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    new_slot = context["new_slot"]
    hold = context["hold"]

    response = context["adapter"].execute(
        reschedule_tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(new_slot.id),
        ),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1


def test_regression_retell_legacy_check_availability_still_works(
    adapter_bundle: AdapterBundle,
) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-legacy-contract",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": CHECK_AVAILABILITY_LEGACY_START,
                    "start_to": CHECK_AVAILABILITY_LEGACY_END,
                    "limit": 1,
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.check_availability_calls) == 1


# ---------------------------------------------------------------------------
# 16. No real provider / dashboard dependency
# ---------------------------------------------------------------------------


def test_clinic_tools_use_in_memory_fakes_not_external_retell_api() -> None:
    context_adapter = make_clinic_context_adapter()
    availability_adapter, availability_scheduling = _make_check_availability_adapter()

    context_response = context_adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-no-network",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )
    availability_response = availability_adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-no-network-availability",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(
                        availability_scheduling.doctors[0].id,
                    ),
                    "date_expression": {"kind": "today"},
                },
            },
        ),
    )

    assert context_response.status == "succeeded"
    assert availability_response.status == "succeeded"
    assert isinstance(
        context_adapter.scheduling_service,
        TrackingSchedulingService,
    )
    assert isinstance(
        availability_scheduling,
        TrackingSchedulingService,
    )
    assert isinstance(context_adapter.voice_calls, TrackingVoiceCallRepository)


def test_disabled_retell_route_never_reaches_adapter() -> None:
    app = create_app()
    settings = Settings(_env_file=None, RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            json={
                "provider_call_id": "retell-call-disabled-contract",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert len(tracking_adapter.execute_calls) == 0
