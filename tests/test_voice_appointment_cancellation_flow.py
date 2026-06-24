"""Voice cancellation flow tests through VoiceAppointmentCancellationService and Retell."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.domain.patient_identity_resolution import PatientIdentityResolutionRequest
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_cancellation import (
    VoiceAppointmentCancellationRequest,
    VoiceCancellationMissingConfirmationError,
)
from app.models.scheduling import AvailabilitySlot, Patient
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.voice_appointment_cancellation import VoiceAppointmentCancellationService
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC
from tests.retell_cancellation_test_support import (
    PROVIDER_CALL_ID,
    cancellation_tool_request,
    create_retell_cancellation_tool_context,
)

PROVIDER_CALL_ID_FLOW = PROVIDER_CALL_ID


def _cancel_request(
    context: dict[str, Any],
    *,
    appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str = "yes, cancel it",
    tool_call_id: str = "tool-call-cancel-flow",
) -> RetellToolCallRequest:
    return cancellation_tool_request(
        appointment_id=appointment_id or str(context["appointment"].id),
        patient_resolution_id=patient_resolution_id or context["patient_resolution_id"],
        explicit_confirmation=explicit_confirmation,
        confirmation_text=confirmation_text,
        tool_call_id=tool_call_id,
    )


def _list_appointments_request(patient_resolution_id: str) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID_FLOW,
            "tool_call_id": "list-after-cancel",
            "tool_name": "list_patient_appointments",
            "arguments": {"patient_resolution_id": patient_resolution_id},
        },
    )


def test_successful_voice_cancellation_cancels_appointment_and_releases_slot() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]
    slot = context["slot"]

    response = context["adapter"].execute(_cancel_request(context))

    assert response.status == "succeeded"
    assert appointment.status == AppointmentStatus.CANCELLED
    assert appointment.cancelled_at is not None
    assert slot.status == AvailabilitySlotStatus.AVAILABLE
    assert response.result["status"] == AppointmentStatus.CANCELLED.value
    assert response.result["appointment_id"] == str(appointment.id)
    assert "cancelled" in response.result["suggested_response_text"].lower()
    assert "Dermatology" in response.result["suggested_response_text"]
    assert len(context["tracking_cancellation"].cancel_calls) == 1


def test_ownership_mismatch_rejects_cancellation() -> None:
    patient_b = Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1-555-0301",
        email="jane.doe@example.test",
    )
    context = create_retell_cancellation_tool_context(extra_patients=[patient_b])
    appointment = context["appointment"]

    resolution_b = context["patient_identity_resolution"].resolve(
        PatientIdentityResolutionRequest(
            patient_name=patient_b.full_name,
            patient_date_of_birth=patient_b.date_of_birth,
            provider_call_id=PROVIDER_CALL_ID_FLOW,
            conversation_id=context["conversation"].id,
            patient_email=patient_b.email,
        ),
    )
    assert resolution_b.patient_resolution_id is not None

    response = context["adapter"].execute(
        _cancel_request(
            context,
            patient_resolution_id=resolution_b.patient_resolution_id,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_not_owned_by_patient"
    assert appointment.status == AppointmentStatus.SCHEDULED
    assert context["slot"].status == AvailabilitySlotStatus.BOOKED
    assert context["tracking_cancellation"].cancel_calls == []


def test_missing_confirmation_rejects_cancellation() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        _cancel_request(context, explicit_confirmation=False),
    )

    assert response.status == "failed"
    assert response.error_code == "missing_explicit_confirmation"
    assert appointment.status == AppointmentStatus.SCHEDULED
    assert context["tracking_cancellation"].cancel_calls == []


def test_blank_confirmation_text_rejects_cancellation() -> None:
    context = create_retell_cancellation_tool_context()
    service: VoiceAppointmentCancellationService = context["adapter"].voice_appointment_cancellation

    with pytest.raises(VoiceCancellationMissingConfirmationError):
        service.cancel_appointment(
            VoiceAppointmentCancellationRequest(
                patient_resolution_id=context["patient_resolution_id"],
                appointment_id=context["appointment"].id,
                explicit_confirmation=True,
                confirmation_text="   ",
                provider_call_id=PROVIDER_CALL_ID_FLOW,
                conversation_id=context["conversation"].id,
                idempotency_key="cancel-blank-confirmation",
            ),
        )


def test_already_cancelled_appointment_returns_safe_idempotent_response() -> None:
    context = create_retell_cancellation_tool_context(status=AppointmentStatus.CANCELLED)
    audit_count_before = len(context["cancellation_context"].audit_logs.records)

    response = context["adapter"].execute(_cancel_request(context))

    assert response.status == "succeeded"
    assert response.result["already_cancelled"] is True
    assert "already cancelled" in response.result["suggested_response_text"].lower()
    assert len(context["cancellation_context"].audit_logs.records) == audit_count_before


def test_past_appointment_cannot_be_cancelled_through_voice_flow() -> None:
    past_start = REFERENCE_CLINIC_NOW_UTC - timedelta(days=2)
    slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=uuid4(),
        start_time=past_start,
        end_time=past_start + timedelta(minutes=30),
        status=AvailabilitySlotStatus.BOOKED,
    )
    context = create_retell_cancellation_tool_context(
        start_time=past_start,
        availability_slot=slot,
    )
    context["appointment"].doctor_id = context["doctor"].id
    context["appointment"].specialty_id = context["specialty"].id
    slot.doctor_id = context["doctor"].id

    response = context["adapter"].execute(_cancel_request(context))

    assert response.status == "failed"
    assert response.error_code == "appointment_not_cancelable"
    assert context["appointment"].status == AppointmentStatus.SCHEDULED


def test_cancelled_appointment_no_longer_appears_in_lookup() -> None:
    context = create_retell_cancellation_tool_context()

    cancel_response = context["adapter"].execute(_cancel_request(context))
    assert cancel_response.status == "succeeded"

    list_response = context["adapter"].execute(
        _list_appointments_request(context["patient_resolution_id"]),
    )

    assert list_response.status == "succeeded"
    assert list_response.result["appointment_count"] == 0
    assert list_response.result["next_step"] == "no_upcoming_appointments"


def test_retell_adapter_invalid_patient_resolution_id_returns_safe_error() -> None:
    context = create_retell_cancellation_tool_context()

    response = context["adapter"].execute(
        _cancel_request(context, patient_resolution_id=str(uuid4())),
    )

    assert response.status == "failed"
    assert response.error_code == "patient_resolution_not_found"
    assert "verify your profile" in response.result["suggested_response_text"]


def test_retell_adapter_success_response_does_not_expose_patient_pii() -> None:
    context = create_retell_cancellation_tool_context()
    patient = context["patient"]

    response = context["adapter"].execute(_cancel_request(context))

    serialized = str(response.result)
    assert response.status == "succeeded"
    assert patient.email not in serialized
    if patient.phone_number is not None:
        assert patient.phone_number not in serialized
    assert str(patient.id) not in serialized
    assert "patient_id" not in response.result


def test_cancellation_service_releases_linked_availability_slot() -> None:
    context = create_retell_cancellation_tool_context()
    service: VoiceAppointmentCancellationService = context["adapter"].voice_appointment_cancellation
    slot = context["slot"]

    service.cancel_appointment(
        VoiceAppointmentCancellationRequest(
            patient_resolution_id=context["patient_resolution_id"],
            appointment_id=context["appointment"].id,
            explicit_confirmation=True,
            confirmation_text="yes, cancel it",
            provider_call_id=PROVIDER_CALL_ID_FLOW,
            conversation_id=context["conversation"].id,
            idempotency_key="cancel-slot-release",
        ),
    )

    assert slot.status == AvailabilitySlotStatus.AVAILABLE
