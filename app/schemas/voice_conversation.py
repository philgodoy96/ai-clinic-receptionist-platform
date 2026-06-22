from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.conversations.enums import ConversationChannel
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_conversation import VoiceConversationDebugContext


class ActiveHoldSummaryResponse(BaseModel):
    hold_id: str
    availability_slot_id: str | None = None
    doctor_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RequestedTimeWindowResponse(BaseModel):
    label: str | None = None
    start: str | None = None
    end: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RescheduleSummaryResponse(BaseModel):
    status: str
    original_appointment_id: str | None = None
    new_appointment_id: str | None = None
    appointment_status: str | None = None
    availability_slot_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    failure_code: str | None = None
    duplicate: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class VoiceConversationContextResponse(BaseModel):
    voice_call_id: UUID
    provider: str
    provider_call_id: str
    call_status: VoiceCallStatus
    conversation_id: UUID | None = None
    conversation_channel: ConversationChannel | None = None
    active_hold_summary: ActiveHoldSummaryResponse | None = None
    requested_specialty: str | None = None
    requested_date: str | None = None
    requested_time_window: RequestedTimeWindowResponse | None = None
    last_selected_slot_id: str | None = None
    last_reschedule_summary: RescheduleSummaryResponse | None = None

    model_config = ConfigDict(from_attributes=True)


_VOICE_CONVERSATION_CONTEXT_RESPONSE_FIELDS = frozenset(
    VoiceConversationContextResponse.model_fields.keys(),
)


def voice_conversation_context_to_response(
    context: VoiceConversationDebugContext,
) -> VoiceConversationContextResponse:
    active_hold_summary = None
    if context.active_hold is not None:
        active_hold_summary = ActiveHoldSummaryResponse.model_validate(context.active_hold)

    requested_time_window = None
    if context.requested_time_window is not None:
        requested_time_window = RequestedTimeWindowResponse.model_validate(
            context.requested_time_window,
        )

    last_reschedule_summary = None
    if context.last_reschedule_summary is not None:
        last_reschedule_summary = RescheduleSummaryResponse.model_validate(
            context.last_reschedule_summary,
        )

    return VoiceConversationContextResponse(
        voice_call_id=context.voice_call_id,
        provider=context.provider,
        provider_call_id=context.provider_call_id,
        call_status=context.call_status,
        conversation_id=context.conversation_id,
        conversation_channel=context.conversation_channel,
        active_hold_summary=active_hold_summary,
        requested_specialty=context.requested_specialty,
        requested_date=context.requested_date,
        requested_time_window=requested_time_window,
        last_selected_slot_id=context.last_selected_slot_id,
        last_reschedule_summary=last_reschedule_summary,
    )


def voice_conversation_context_response_field_names() -> frozenset[str]:
    return _VOICE_CONVERSATION_CONTEXT_RESPONSE_FIELDS
