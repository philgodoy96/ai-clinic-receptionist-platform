"""Voice rescheduling flow tests through VoiceAppointmentReschedulingService and Retell."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain.audit.enums import AuditEventType
from app.domain.patient_identity_resolution import PatientIdentityResolutionRequest
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_rescheduling import (
    VoiceAppointmentReschedulingRequest,
    VoiceReschedulingMissingConfirmationError,
)
from app.models.scheduling import AvailabilitySlot, Patient
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.voice_appointment_rescheduling import VoiceAppointmentReschedulingService
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC
from tests.retell_rescheduling_test_support import (
    PROVIDER_CALL_ID,
    create_retell_rescheduling_tool_context,
    reschedule_tool_request,
)


def _reschedule_request(
    context: dict[str, Any],
    *,
    appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str = "yes, reschedule it",
    tool_call_id: str = "tool-call-reschedule-flow",
) -> RetellToolCallRequest:
    return reschedule_tool_request(
        appointment_id=appointment_id or str(context["original_appointment"].id),
        patient_resolution_id=patient_resolution_id or context["patient_resolution_id"],
        hold_id=hold_id or str(context["hold"].hold_id),
        new_slot_id=new_slot_id or str(context["new_slot"].id),
        explicit_confirmation=explicit_confirmation,
        confirmation_text=confirmation_text,
        tool_call_id=tool_call_id,
    )


def _list_appointments_request(patient_resolution_id: str) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": "list-after-reschedule",
            "tool_name": "list_patient_appointments",
            "arguments": {"patient_resolution_id": patient_resolution_id},
        },
    )


def _scheduled_appointments_for_patient(
    repository: Any,
    *,
    patient_id: UUID,
) -> list[Any]:
    return [
        appointment
        for appointment in repository.appointments
        if appointment.patient_id == patient_id
        and appointment.status == AppointmentStatus.SCHEDULED
    ]


def test_successful_voice_reschedule_updates_appointments_and_slots() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    original_slot = context["original_slot"]
    new_slot = context["new_slot"]

    response = context["adapter"].execute(_reschedule_request(context))

    assert response.status == "succeeded"
    assert original_appointment.status == AppointmentStatus.RESCHEDULED
    assert original_slot.status == AvailabilitySlotStatus.AVAILABLE
    assert new_slot.status == AvailabilitySlotStatus.BOOKED
    assert response.result["status"] == AppointmentStatus.SCHEDULED.value
    assert response.result["new_appointment_id"] is not None
    assert "rescheduled" in response.result["suggested_response_text"].lower()
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1

    scheduled = _scheduled_appointments_for_patient(
        context["appointment_repository"],
        patient_id=context["patient"].id,
    )
    assert len(scheduled) == 1
    assert scheduled[0].id != original_appointment.id
    assert scheduled[0].rescheduled_from_appointment_id == original_appointment.id


def test_ownership_mismatch_rejects_reschedule() -> None:
    patient_b = Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1-555-0301",
        email="jane.doe@example.test",
    )
    context = create_retell_rescheduling_tool_context(extra_patients=[patient_b])
    original_appointment = context["original_appointment"]
    original_slot = context["original_slot"]
    new_slot = context["new_slot"]

    resolution_b = context["patient_identity_resolution"].resolve(
        PatientIdentityResolutionRequest(
            patient_name=patient_b.full_name,
            patient_date_of_birth=patient_b.date_of_birth,
            provider_call_id=PROVIDER_CALL_ID,
            conversation_id=context["conversation"].id,
            patient_email=patient_b.email,
        ),
    )
    assert resolution_b.patient_resolution_id is not None

    response = context["adapter"].execute(
        _reschedule_request(
            context,
            patient_resolution_id=resolution_b.patient_resolution_id,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_not_owned_by_patient"
    assert original_appointment.status == AppointmentStatus.SCHEDULED
    assert original_slot.status == AvailabilitySlotStatus.BOOKED
    assert new_slot.status == AvailabilitySlotStatus.AVAILABLE
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_missing_confirmation_rejects_reschedule() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]

    response = context["adapter"].execute(
        _reschedule_request(context, explicit_confirmation=False),
    )

    assert response.status == "failed"
    assert response.error_code == "missing_explicit_confirmation"
    assert original_appointment.status == AppointmentStatus.SCHEDULED
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_blank_confirmation_text_rejects_reschedule() -> None:
    context = create_retell_rescheduling_tool_context()
    service: VoiceAppointmentReschedulingService = (
        context["adapter"].voice_appointment_rescheduling
    )

    with pytest.raises(VoiceReschedulingMissingConfirmationError):
        service.reschedule_appointment(
            VoiceAppointmentReschedulingRequest(
                patient_resolution_id=context["patient_resolution_id"],
                appointment_id=context["original_appointment"].id,
                new_slot_id=context["new_slot"].id,
                hold_id=context["hold"].hold_id,
                explicit_confirmation=True,
                confirmation_text="   ",
                provider_call_id=PROVIDER_CALL_ID,
                conversation_id=context["conversation"].id,
                idempotency_key="reschedule-blank-confirmation",
            ),
        )


def test_cancelled_appointment_cannot_be_rescheduled() -> None:
    context = create_retell_rescheduling_tool_context(
        original_status=AppointmentStatus.CANCELLED,
    )

    response = context["adapter"].execute(_reschedule_request(context))

    assert response.status == "failed"
    assert response.error_code == "appointment_not_reschedulable"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_already_rescheduled_appointment_returns_safe_idempotent_response() -> None:
    context = create_retell_rescheduling_tool_context()
    first = context["adapter"].execute(_reschedule_request(context, tool_call_id="first"))
    assert first.status == "succeeded"

    original_appointment = context["original_appointment"]
    assert original_appointment.status == AppointmentStatus.RESCHEDULED

    second = context["adapter"].execute(
        _reschedule_request(context, tool_call_id="second-attempt"),
    )

    assert second.status == "succeeded"
    assert second.result["already_rescheduled"] is True
    scheduled = _scheduled_appointments_for_patient(
        context["appointment_repository"],
        patient_id=context["patient"].id,
    )
    assert len(scheduled) == 1


def test_past_appointment_cannot_be_rescheduled_through_voice_flow() -> None:
    past_start = REFERENCE_CLINIC_NOW_UTC - timedelta(days=2)
    context = create_rescheduling_context_with_past_appointment(past_start)

    response = context["adapter"].execute(_reschedule_request(context))

    assert response.status == "failed"
    assert response.error_code == "appointment_not_reschedulable"
    assert context["original_appointment"].status == AppointmentStatus.SCHEDULED


def test_missing_hold_rejects_reschedule() -> None:
    context = create_retell_rescheduling_tool_context()

    response = context["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "missing-hold",
                "tool_name": "reschedule_appointment",
                "arguments": {
                    "appointment_id": str(context["original_appointment"].id),
                    "patient_resolution_id": context["patient_resolution_id"],
                    "new_slot_id": str(context["new_slot"].id),
                    "explicit_confirmation": True,
                    "confirmation_text": "yes",
                },
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "retell_tool_arguments_invalid"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_hold_slot_mismatch_rejects_reschedule() -> None:
    context = create_retell_rescheduling_tool_context()
    mismatched_slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=context["rescheduling_context"].doctor.id,
        start_time=context["new_slot"].start_time + timedelta(days=1),
        end_time=context["new_slot"].end_time + timedelta(days=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    context["rescheduling_context"].slot_repository.slots.append(mismatched_slot)

    response = context["adapter"].execute(
        _reschedule_request(
            context,
            new_slot_id=str(mismatched_slot.id),
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_hold_slot_mismatch"
    assert context["original_appointment"].status == AppointmentStatus.SCHEDULED


def test_new_slot_unavailable_rejects_reschedule() -> None:
    context = create_retell_rescheduling_tool_context(
        new_slot_status=AvailabilitySlotStatus.BOOKED,
    )

    response = context["adapter"].execute(_reschedule_request(context))

    assert response.status == "failed"
    assert response.error_code == "new_slot_not_available"
    assert context["original_appointment"].status == AppointmentStatus.SCHEDULED


def test_duplicate_retell_tool_call_is_idempotent() -> None:
    context = create_retell_rescheduling_tool_context()
    request = _reschedule_request(context, tool_call_id="duplicate-reschedule")

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    scheduled = _scheduled_appointments_for_patient(
        context["appointment_repository"],
        patient_id=context["patient"].id,
    )
    assert len(scheduled) == 1


def test_retell_adapter_invalid_patient_resolution_id_returns_safe_error() -> None:
    context = create_retell_rescheduling_tool_context()

    response = context["adapter"].execute(
        _reschedule_request(context, patient_resolution_id=str(uuid4())),
    )

    assert response.status == "failed"
    assert response.error_code == "patient_resolution_not_found"
    assert "patient_id" not in str(response.result).lower()
    assert "redis" not in str(response.result).lower()


def test_successful_reschedule_writes_audit_log() -> None:
    context = create_retell_rescheduling_tool_context()
    audit_logs = context["rescheduling_context"].audit_logs

    response = context["adapter"].execute(_reschedule_request(context))

    assert response.status == "succeeded"
    event_types = [record.event_type for record in audit_logs.records]
    assert AuditEventType.APPOINTMENT_RESCHEDULE_SUCCEEDED in event_types


def test_list_patient_appointments_shows_new_not_old_after_reschedule() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]

    reschedule_response = context["adapter"].execute(_reschedule_request(context))
    assert reschedule_response.status == "succeeded"
    new_appointment_id = reschedule_response.result["new_appointment_id"]

    list_response = context["adapter"].execute(
        _list_appointments_request(context["patient_resolution_id"]),
    )

    assert list_response.status == "succeeded"
    assert list_response.result["appointment_count"] == 1
    assert list_response.result["appointments"][0]["appointment_id"] == new_appointment_id
    assert list_response.result["appointments"][0]["appointment_id"] != str(original_appointment.id)


def create_rescheduling_context_with_past_appointment(
    past_start: Any,
) -> dict[str, Any]:
    slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=uuid4(),
        start_time=past_start,
        end_time=past_start + timedelta(minutes=30),
        status=AvailabilitySlotStatus.BOOKED,
    )
    context = create_retell_rescheduling_tool_context()
    context["original_appointment"].start_time = past_start
    context["original_appointment"].end_time = past_start + timedelta(minutes=30)
    context["original_appointment"].availability_slot_id = slot.id
    context["original_slot"].start_time = past_start
    context["original_slot"].end_time = past_start + timedelta(minutes=30)
    context["rescheduling_context"].slot_repository.slots = [
        context["original_slot"],
        context["new_slot"],
    ]
    return context
