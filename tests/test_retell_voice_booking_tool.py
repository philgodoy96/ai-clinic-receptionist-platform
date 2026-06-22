from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_retell_tool_calling_adapter
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.main import create_app
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_booking import AppointmentBookingService
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
)
from tests.test_appointment_booking_api import FakeAuditLogService, FakeDatabaseSession
from tests.test_appointment_booking_service import create_booking_context
from tests.test_chat_receptionist_service import TrackingAppointmentBookingService
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository
from tests.test_voice_booking_confirmation_service import FakeVoiceBookingAttemptRepository

PROVIDER_CALL_ID = "retell-call-book-1"
TOOL_CALL_ID = "tool-call-book-1"


def _booking_arguments(
    *,
    hold_id: str,
    slot_id: str,
    explicit_confirmation: bool = True,
) -> dict[str, object]:
    return {
        "hold_id": hold_id,
        "slot_id": slot_id,
        "patient_name": "John Miller",
        "patient_date_of_birth": "1985-04-12",
        "patient_email": "john.miller@example.test",
        "patient_phone": "+1-555-0201",
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": "Yes, please book it.",
        "notes": "Annual checkup",
    }


def _tool_request(
    *,
    hold_id: str,
    slot_id: str,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "book_appointment",
            "arguments": _booking_arguments(
                hold_id=hold_id,
                slot_id=slot_id,
                explicit_confirmation=explicit_confirmation,
            ),
        },
    )


def create_retell_booking_tool_context() -> dict[str, Any]:
    booking_context = create_booking_context()
    hold = booking_context.hold_service.create_hold(
        availability_slot_id=booking_context.slot.id,
        doctor_id=booking_context.doctor.id,
        start_time=booking_context.slot.start_time,
        end_time=booking_context.slot.end_time,
        owner_id=PROVIDER_CALL_ID,
    )

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        conversation_metadata={
            "voice_context": {
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(booking_context.slot.id),
                "start_time": booking_context.slot.start_time.isoformat(),
                "end_time": booking_context.slot.end_time.isoformat(),
            },
        },
    )
    conversation_repository.conversations.append(conversation)

    voice_calls = FakeVoiceCallRepository()
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=PROVIDER_CALL_ID,
        status=VoiceCallStatus.IN_PROGRESS,
        conversation_id=conversation.id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)

    conversations = ConversationService(repository=conversation_repository)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=conversations,
    )

    inner_booking = booking_context.booking_service
    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=inner_booking.doctors,
        patients=inner_booking.patients,
        availability_slots=inner_booking.availability_slots,
        appointments=inner_booking.appointments,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    email_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_repository)
    audit_logs = FakeAuditLogService()
    db = FakeDatabaseSession()
    voice_booking_attempts = FakeVoiceBookingAttemptRepository()

    voice_booking_confirmation = VoiceBookingConfirmationService(
        db=cast(Session, db),
        booking_service=cast(AppointmentBookingService, tracking_booking),
        hold_service=booking_context.hold_service,
        scheduling_service=scheduling_service,
        conversations=conversations,
        audit_logs=cast(AuditLogService, audit_logs),
        email_jobs=email_jobs,
        voice_booking_attempts=voice_booking_attempts,
        appointments=booking_context.appointment_repository,
        availability_slots=inner_booking.availability_slots,
    )

    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=booking_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        voice_booking_confirmation=voice_booking_confirmation,
        appointments=booking_context.appointment_repository,
    )

    return {
        "adapter": adapter,
        "tracking_booking": tracking_booking,
        "booking_context": booking_context,
        "email_repository": email_repository,
        "appointment_repository": booking_context.appointment_repository,
    }


def _hold_id(booking_context: Any) -> str:
    return str(next(iter(booking_context.hold_repository.holds.values())).hold_id)


def test_valid_booking_tool_creates_appointment_through_booking_service() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1
    assert response.result["appointment_id"] is not None
    assert response.result["status"] == "scheduled"
    assert response.result["email_confirmation_queued"] is True
    assert response.result["appointment"]["status"] == AppointmentStatus.SCHEDULED.value
    assert response.duplicate is False


def test_no_active_hold_rejects() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold = next(iter(booking_context.hold_repository.holds.values()))
    booking_context.hold_repository.delete(
        doctor_id=hold.doctor_id,
        start_time=hold.start_time,
    )

    response = context["adapter"].execute(
        _tool_request(hold_id=str(hold.hold_id), slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_hold_expired"
    assert context["tracking_booking"].book_calls == []


def test_missing_confirmation_rejects() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(
            hold_id=hold_id,
            slot_id=str(booking_context.slot.id),
            explicit_confirmation=False,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "booking_confirmation_required"
    assert context["tracking_booking"].book_calls == []


def test_duplicate_tool_callback_does_not_duplicate_appointment() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)
    request = _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id))

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context["tracking_booking"].book_calls) == 1
    assert len(context["appointment_repository"].appointments) == 1


def test_duplicate_tool_callback_does_not_duplicate_email_job() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)
    request = _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id))

    context["adapter"].execute(request)
    email_count = len(context["email_repository"].email_jobs)
    context["adapter"].execute(request)

    assert len(context["email_repository"].email_jobs) == email_count


def test_booking_tool_still_works_alongside_cancellation_tool() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1
    assert response.result["status"] == "scheduled"


def test_unknown_tool_still_rejected() -> None:
    context = create_retell_booking_tool_context()

    response = context["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_name": "delete_appointment",
                "arguments": {},
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "unsupported_retell_tool"
    assert context["tracking_booking"].book_calls == []


def test_adapter_does_not_insert_appointment_directly() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)
    appointments_before = len(context["appointment_repository"].appointments)

    context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert len(context["appointment_repository"].appointments) == appointments_before + 1
    assert len(context["tracking_booking"].book_calls) == 1


def test_success_response_has_no_raw_payload_or_secrets() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    serialized = response.model_dump(mode="json")
    payload_text = str(serialized).lower()

    assert response.status == "succeeded"
    assert "traceback" not in payload_text
    assert "webhook_secret" not in payload_text
    assert "api_key" not in payload_text
    assert "transcript" not in payload_text


def test_invalid_signature_prevents_booking() -> None:
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
                b'{"provider_call_id":"retell-call-123","tool_name":"book_appointment",'
                b'"tool_call_id":"tool-call-1","arguments":{"hold_id":"'
                + str(uuid4()).encode()
                + b'","patient_name":"Jane Doe","patient_date_of_birth":"1990-05-15",'
                b'"patient_email":"jane.doe@example.com","explicit_confirmation":true}}'
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


def test_verified_route_booking_tool_returns_provider_safe_response() -> None:
    booking_context = create_retell_booking_tool_context()
    hold_id = _hold_id(booking_context["booking_context"])

    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    app.dependency_overrides[get_retell_tool_calling_adapter] = (
        lambda: booking_context["adapter"]
    )

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(
                    hold_id=hold_id,
                    slot_id=str(booking_context["booking_context"].slot.id),
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["appointment_id"]
    assert body["result"]["status"] == "scheduled"
    assert "traceback" not in str(body).lower()


def test_retell_disabled_rejects_booking_route() -> None:
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
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(
                    hold_id=str(uuid4()),
                    slot_id=str(uuid4()),
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == []


def test_booking_tool_does_not_trigger_llm() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    with patch("app.ai.provider_factory.build_llm_provider") as llm_factory_mock:
        response = context["adapter"].execute(
            _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
        )

    assert response.status == "succeeded"
    llm_factory_mock.assert_not_called()
