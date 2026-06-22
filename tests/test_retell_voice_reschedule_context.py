from __future__ import annotations

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_conversation import read_last_reschedule_summary, read_voice_context
from tests.retell_rescheduling_test_support import (
    PROVIDER_CALL_ID,
    TOOL_CALL_ID,
    create_retell_rescheduling_tool_context,
    reschedule_tool_request,
)


def test_successful_reschedule_updates_conversation_context() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    conversation = context["conversation"]
    conversation.conversation_metadata["locale"] = "en-US"

    response = context["adapter"].execute(
        reschedule_tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"

    stored = context["conversation_repository"].get_by_id(conversation.id)
    assert stored is not None
    voice_context = read_voice_context(stored.conversation_metadata)
    summary = read_last_reschedule_summary(stored.conversation_metadata)

    assert voice_context["appointment_id"] == str(response.result["new_appointment_id"])
    assert voice_context["appointment_status"] == AppointmentStatus.SCHEDULED.value
    assert voice_context["rescheduled_from_appointment_id"] == str(original_appointment.id)
    assert voice_context["availability_slot_id"] == str(context["new_slot"].id)
    assert stored.conversation_metadata["locale"] == "en-US"
    assert summary is not None
    assert summary.status == "succeeded"
    assert summary.original_appointment_id == str(original_appointment.id)
    assert summary.new_appointment_id == str(response.result["new_appointment_id"])


def test_active_hold_cleared_on_success() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        reschedule_tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"

    bridge_context = context["bridge"].get_context_for_provider_call("retell", PROVIDER_CALL_ID)
    assert bridge_context.active_hold is None


def test_recoverable_failure_preserves_useful_hold_context() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    conversation = context["conversation"]
    blocked_slot = context["new_slot"]
    blocked_slot.status = AvailabilitySlotStatus.BLOCKED
    voice_context_before = read_voice_context(conversation.conversation_metadata)

    response = context["adapter"].execute(
        reschedule_tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(blocked_slot.id),
            tool_call_id="tool-call-reschedule-recoverable",
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "slot_unavailable"

    stored = context["conversation_repository"].get_by_id(conversation.id)
    assert stored is not None
    voice_context_after = read_voice_context(stored.conversation_metadata)

    assert voice_context_after.get("hold_id") == voice_context_before.get("hold_id")
    assert voice_context_after.get("availability_slot_id") == voice_context_before.get(
        "availability_slot_id",
    )
    assert read_last_reschedule_summary(stored.conversation_metadata) is None


def test_duplicate_callback_does_not_corrupt_metadata() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    conversation = context["conversation"]
    request = reschedule_tool_request(
        original_appointment_id=str(original_appointment.id),
        hold_id=str(hold.hold_id),
        new_slot_id=str(context["new_slot"].id),
        tool_call_id=TOOL_CALL_ID,
    )

    first = context["adapter"].execute(request)
    stored_after_first = context["conversation_repository"].get_by_id(conversation.id)
    assert stored_after_first is not None
    voice_context_after_first = read_voice_context(stored_after_first.conversation_metadata)
    summary_after_first = read_last_reschedule_summary(
        stored_after_first.conversation_metadata,
    )

    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True

    stored_after_second = context["conversation_repository"].get_by_id(conversation.id)
    assert stored_after_second is not None
    voice_context_after_second = read_voice_context(stored_after_second.conversation_metadata)
    summary_after_second = read_last_reschedule_summary(
        stored_after_second.conversation_metadata,
    )

    assert voice_context_after_second == voice_context_after_first
    assert summary_after_second is not None
    assert summary_after_first is not None
    assert summary_after_second == summary_after_first


def test_debug_context_returns_safe_reschedule_summary() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        reschedule_tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
            tool_call_id="tool-call-reschedule-debug",
        ),
    )

    assert response.status == "succeeded"

    debug_context = context["bridge"].get_debug_context_for_voice_call(context["voice_call"].id)

    assert debug_context.last_reschedule_summary is not None
    assert debug_context.last_reschedule_summary.status == "succeeded"
    assert debug_context.last_reschedule_summary.original_appointment_id == str(
        original_appointment.id,
    )
    assert debug_context.last_reschedule_summary.new_appointment_id == str(
        response.result["new_appointment_id"],
    )
    serialized = repr(debug_context)
    assert "transcript" not in serialized
    assert "webhook_secret" not in serialized
