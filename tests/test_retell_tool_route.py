from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_appointment_booking_service,
    get_email_job_service,
    get_retell_signature_verifier,
    get_retell_tool_calling_adapter,
    get_scheduling_service,
)
from app.db.session import get_db
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.integrations.retell.signature import HmacRetellSignatureVerifier
from app.main import create_app
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.models.voice_calls import VoiceCall
from app.services.appointment_holds import AppointmentHoldService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.demo_guardrail_support import (
    create_guarded_retell_app,
    make_guardrail_settings,
)
from tests.retell_cancellation_test_support import (
    PROVIDER_CALL_ID,
    TOOL_CALL_ID,
    cancellation_arguments,
    create_retell_cancellation_tool_context,
)
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    TrackingRetellToolCallingAdapter,
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_appointment_hold_api import FakeDatabaseSession
from tests.test_retell_read_tools import FakeSchedulingService


@pytest.fixture()
def route_context() -> RouteContext:
    specialty_id = uuid4()
    doctor_id = uuid4()
    slot_id = uuid4()
    start_time = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)

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
    slot = AvailabilitySlot(
        id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    scheduling_service = FakeSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        patients=[],
        availability_slots=[slot],
        appointments=[],
    )
    hold_repository = RouteAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    voice_calls = RouteVoiceCallRepository()
    adapter = RetellToolCallingAdapter(
        scheduling_service=cast(SchedulingService, scheduling_service),
        hold_service=hold_service,
        voice_calls=voice_calls,
        clinic_time_service=make_test_clinic_time_service(),
    )
    booking_service = NeverCalledBookingService()
    email_service = NeverCalledEmailService()

    return RouteContext(
        specialty=specialty,
        doctor=doctor,
        slot=slot,
        scheduling_service=scheduling_service,
        hold_repository=hold_repository,
        hold_service=hold_service,
        voice_calls=voice_calls,
        adapter=adapter,
        booking_service=booking_service,
        email_service=email_service,
    )


@pytest.fixture()
def secured_client(
    route_context: RouteContext,
) -> Generator[tuple[TestClient, TrackingRetellToolCallingAdapter], None, None]:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    tracking_adapter = TrackingRetellToolCallingAdapter(route_context.adapter)
    db = FakeDatabaseSession()

    def override_scheduling_service() -> FakeSchedulingService:
        return route_context.scheduling_service

    def override_adapter() -> TrackingRetellToolCallingAdapter:
        return tracking_adapter

    def override_booking_service() -> NeverCalledBookingService:
        return route_context.booking_service

    def override_email_service() -> NeverCalledEmailService:
        return route_context.email_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    app.dependency_overrides[get_scheduling_service] = override_scheduling_service
    app.dependency_overrides[get_retell_tool_calling_adapter] = override_adapter
    app.dependency_overrides[get_appointment_booking_service] = override_booking_service
    app.dependency_overrides[get_email_job_service] = override_email_service
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        yield client, tracking_adapter

    app.dependency_overrides.clear()


class RouteContext:
    def __init__(
        self,
        *,
        specialty: Specialty,
        doctor: Doctor,
        slot: AvailabilitySlot,
        scheduling_service: FakeSchedulingService,
        hold_repository: RouteAppointmentHoldRepository,
        hold_service: AppointmentHoldService,
        voice_calls: RouteVoiceCallRepository,
        adapter: RetellToolCallingAdapter,
        booking_service: NeverCalledBookingService,
        email_service: NeverCalledEmailService,
    ) -> None:
        self.specialty = specialty
        self.doctor = doctor
        self.slot = slot
        self.scheduling_service = scheduling_service
        self.hold_repository = hold_repository
        self.hold_service = hold_service
        self.voice_calls = voice_calls
        self.adapter = adapter
        self.booking_service = booking_service
        self.email_service = email_service


def test_valid_check_availability_tool_call_returns_slots(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, tracking_adapter = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-123",
            "tool_name": "check_availability",
            "arguments": {
                "doctor_id": str(route_context.doctor.id),
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert len(body["result"]["available_slots"]) == 1
    assert len(tracking_adapter.execute_calls) == 1


def test_valid_signature_allows_adapter_call(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, tracking_adapter = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-signed",
            "tool_name": "check_availability",
            "arguments": {
                "doctor_id": str(route_context.doctor.id),
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
        },
    )

    assert response.status_code == 200
    assert len(tracking_adapter.execute_calls) == 1


def test_malformed_json_returns_standardized_error(route_context: RouteContext) -> None:
    app = create_app()
    timestamp_ms = 1_700_000_000_000
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret="test-webhook-secret",
        now_millis=lambda: timestamp_ms,
    )

    raw_body = b"{not-json"
    signature = sign_retell_body(
        raw_body=raw_body,
        secret="test-webhook-secret",
        timestamp_ms=timestamp_ms,
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

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert tracking_adapter.execute_calls == []


def test_retell_disabled_rejects_unified_route() -> None:
    app = create_app()
    settings = make_retell_enabled_settings(RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                },
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == []


def test_response_does_not_expose_raw_payload_or_secrets(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()
    secret = settings.retell_webhook_secret or ""

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-123",
            "tool_name": "check_availability",
            "arguments": {
                "doctor_id": str(route_context.doctor.id),
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
            "webhook_secret": secret,
            "transcript": "patient said secret things",
            "raw_payload": {"api_key": "hidden"},
        },
    )

    assert response.status_code == 200
    body_text = response.text
    assert secret not in body_text
    assert "patient said secret things" not in body_text
    assert "hidden" not in body_text
    assert "raw_payload" not in response.json()


def test_hold_tool_call_creates_only_hold_not_appointment(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-456",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(route_context.slot.id),
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert len(route_context.hold_repository.create_calls) == 1
    assert route_context.scheduling_service.appointments == []
    assert route_context.booking_service.calls == []


def test_release_tool_call_does_not_cancel_appointment(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    from app.domain.scheduling.enums import AppointmentStatus
    from app.models.scheduling import Appointment

    appointment = Appointment(
        id=uuid4(),
        patient_id=uuid4(),
        doctor_id=route_context.doctor.id,
        specialty_id=route_context.specialty.id,
        availability_slot_id=route_context.slot.id,
        start_time=route_context.slot.start_time,
        end_time=route_context.slot.end_time,
        status=AppointmentStatus.SCHEDULED,
        reason="Skin check",
    )
    route_context.scheduling_service.appointments = [appointment]

    hold = AppointmentHold.create(
        availability_slot_id=route_context.slot.id,
        doctor_id=route_context.slot.doctor_id,
        start_time=route_context.slot.start_time,
        end_time=route_context.slot.end_time,
        owner_id="retell-call-789",
    )
    route_context.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    route_context.hold_repository.holds_by_id[hold.hold_id] = hold

    client, _ = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-789",
            "tool_name": "release_appointment_hold",
            "arguments": {
                "hold_id": str(hold.hold_id),
            },
        },
    )

    assert response.status_code == 200
    assert len(route_context.scheduling_service.appointments) == 1
    assert route_context.scheduling_service.appointments[0].status == AppointmentStatus.SCHEDULED
    assert route_context.booking_service.calls == []


def test_check_availability_tool_call_has_no_side_effects(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()

    post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-123",
            "tool_name": "check_availability",
            "arguments": {
                "doctor_id": str(route_context.doctor.id),
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
        },
    )

    assert route_context.hold_repository.create_calls == []
    assert route_context.hold_repository.delete_calls == []
    assert route_context.voice_calls.outcomes == {}
    assert route_context.booking_service.calls == []


def test_duplicate_release_tool_call_is_idempotent(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()
    hold = AppointmentHold.create(
        availability_slot_id=route_context.slot.id,
        doctor_id=route_context.slot.doctor_id,
        start_time=route_context.slot.start_time,
        end_time=route_context.slot.end_time,
        owner_id="retell-call-789",
    )
    route_context.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    route_context.hold_repository.holds_by_id[hold.hold_id] = hold

    payload = {
        "provider_call_id": "retell-call-789",
        "tool_call_id": "release-call-dup",
        "tool_name": "release_appointment_hold",
        "arguments": {
            "hold_id": str(hold.hold_id),
        },
    }

    first = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body=payload,
    )
    second = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(route_context.hold_repository.delete_calls) == 1


def test_valid_hold_tool_call_creates_hold(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-456",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(route_context.slot.id),
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert len(route_context.hold_repository.create_calls) == 1
    assert "hold_id" in body["result"]


def test_duplicate_hold_tool_call_does_not_duplicate_hold(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()
    payload = {
        "provider_call_id": "retell-call-456",
        "tool_call_id": "hold-call-dup",
        "tool_name": "hold_appointment_slot",
        "arguments": {
            "availability_slot_id": str(route_context.slot.id),
        },
    }

    first = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body=payload,
    )
    second = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(route_context.hold_repository.create_calls) == 1


def test_valid_release_tool_call_releases_hold(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()
    hold = AppointmentHold.create(
        availability_slot_id=route_context.slot.id,
        doctor_id=route_context.slot.doctor_id,
        start_time=route_context.slot.start_time,
        end_time=route_context.slot.end_time,
        owner_id="retell-call-789",
    )
    route_context.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    route_context.hold_repository.holds_by_id[hold.hold_id] = hold

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-789",
            "tool_name": "release_appointment_hold",
            "arguments": {
                "hold_id": str(hold.hold_id),
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert route_context.hold_repository.delete_calls == [
        (hold.doctor_id, hold.start_time),
    ]


def test_invalid_signature_prevents_adapter_call(
    route_context: RouteContext,
) -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()

    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=(
                b'{"provider_call_id":"retell-call-123","tool_name":"check_availability",'
                b'"arguments":{"doctor_id":"'
                + str(route_context.doctor.id).encode()
                + b'","start_from":"2026-07-01T13:00:00Z","start_to":"2026-07-01T17:00:00Z"}}'
            ),
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.execute_calls == []


def test_unknown_tool_rejected(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, tracking_adapter = secured_client
    settings = make_secured_retell_settings()

    response = post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-123",
            "tool_name": "delete_appointment",
            "arguments": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["error_code"] == "unsupported_retell_tool"
    assert route_context.hold_repository.create_calls == []
    assert len(tracking_adapter.execute_calls) == 1


def test_booking_service_is_not_called(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()

    post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-123",
            "tool_name": "check_availability",
            "arguments": {
                "doctor_id": str(route_context.doctor.id),
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
        },
    )

    assert route_context.booking_service.calls == []


def test_email_service_is_not_called(
    secured_client: tuple[TestClient, TrackingRetellToolCallingAdapter],
    route_context: RouteContext,
) -> None:
    client, _ = secured_client
    settings = make_secured_retell_settings()

    post_retell_tool(
        client,
        "/api/v1/retell/tools",
        settings=settings,
        json_body={
            "provider_call_id": "retell-call-456",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(route_context.slot.id),
            },
        },
    )

    assert route_context.email_service.calls == []


def test_verified_cancel_appointment_route_succeeds() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    tracking_adapter = TrackingRetellToolCallingAdapter(context["adapter"])

    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "cancel_appointment",
                "arguments": cancellation_arguments(
                    appointment_id=str(appointment.id),
                    patient_resolution_id=context["patient_resolution_id"],
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["appointment_id"] == str(appointment.id)
    assert body["result"]["status"] == AppointmentStatus.CANCELLED.value
    assert len(tracking_adapter.execute_calls) == 1


def test_cancel_appointment_route_rejects_invalid_signature() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()

    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=(
                b'{"provider_call_id":"retell-call-cancel-route","tool_name":"cancel_appointment",'
                b'"tool_call_id":"tool-call-cancel-route","arguments":{"appointment_id":"'
                + str(uuid4()).encode()
                + b'","explicit_confirmation":true}}'
            ),
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.execute_calls == []


def test_cancel_appointment_route_does_not_call_email_service() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]
    email_service = NeverCalledEmailService()

    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: (
        TrackingRetellToolCallingAdapter(context["adapter"])
    )
    app.dependency_overrides[get_email_job_service] = lambda: email_service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "tool-call-cancel-route-email",
                "tool_name": "cancel_appointment",
                "arguments": cancellation_arguments(
                    appointment_id=str(appointment.id),
                    patient_resolution_id=context["patient_resolution_id"],
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert email_service.calls == []


def test_public_demo_guardrails_still_apply() -> None:
    app, redis_client = create_guarded_retell_app(
        make_guardrail_settings(DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1),
    )

    with TestClient(app) as client:
        payload = {
            "provider_call_id": "retell-call-guardrail",
            "tool_name": "check_availability",
            "arguments": {
                "start_from": "2026-07-01T13:00:00Z",
                "start_to": "2026-07-01T17:00:00Z",
            },
        }
        first = client.post("/api/v1/retell/tools", json=payload)
        second = client.post("/api/v1/retell/tools", json=payload)

    app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "demo_guardrail_limit_exceeded"
    assert len(redis_client.values) == 2


class RouteAppointmentHoldRepository:
    def __init__(self) -> None:
        self.holds: dict[tuple[UUID, datetime], AppointmentHold] = {}
        self.holds_by_id: dict[UUID, AppointmentHold] = {}
        self.create_calls: list[AppointmentHold] = []
        self.delete_calls: list[tuple[UUID, datetime]] = []

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        key = (hold.doctor_id, hold.start_time)

        if key in self.holds:
            return False

        self.holds[key] = hold
        self.holds_by_id[hold.hold_id] = hold
        self.create_calls.append(hold)

        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        return self.holds.get((doctor_id, start_time))

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        return self.holds_by_id.get(hold_id)

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        hold = self.holds.pop((doctor_id, start_time), None)
        self.delete_calls.append((doctor_id, start_time))

        if hold is not None:
            self.holds_by_id.pop(hold.hold_id, None)


class RouteVoiceCallRepository:
    def __init__(self) -> None:
        self.voice_calls: dict[str, VoiceCall] = {}
        self.outcomes: dict[str, dict[str, Any]] = {}

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        _ = provider
        return self.voice_calls.get(provider_call_id)

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        existing = self.get_by_provider_call_id(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        if existing is not None:
            return existing, False

        voice_call = VoiceCall(
            id=uuid4(),
            provider=provider,
            provider_call_id=provider_call_id,
            status=VoiceCallStatus.CREATED,
        )
        self.voice_calls[provider_call_id] = voice_call

        return voice_call, True

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        return self.outcomes.get(idempotency_key)

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        _ = (
            voice_call_id,
            provider,
            provider_call_id,
            event_type,
            tool_call_id,
            occurred_at,
        )

        if idempotency_key in self.outcomes:
            return False

        self.outcomes[idempotency_key] = outcome

        return True


class NeverCalledBookingService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def book_appointment(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("book_appointment")
        raise AssertionError("booking service must not be called")


class NeverCalledEmailService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_by_idempotency_key(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("get_by_idempotency_key")
        raise AssertionError("email service must not be called")
