from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.errors import (
    INVALID_RETELL_PAYLOAD_CODE,
    RETELL_TOOL_ARGUMENTS_INVALID_CODE,
    RETELL_TOOL_PROVIDER_CALL_ID_REQUIRED_CODE,
    UNSUPPORTED_RETELL_TOOL_CODE,
)
from app.core.request_context import get_correlation_id, get_request_id
from app.domain.appointment_rescheduling import (
    AppointmentReschedulingError,
    AppointmentReschedulingHoldExpiredError,
    AppointmentReschedulingMissingConfirmationError,
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
    AppointmentReschedulingResult,
    AppointmentReschedulingSlotAlreadyBookedError,
    AppointmentReschedulingSlotNotFoundError,
    AppointmentReschedulingSlotUnavailableError,
    normalize_rescheduling_reason,
)
from app.domain.appointments import (
    AppointmentCancellationMissingConfirmationError,
    AppointmentCancellationRequest,
    AppointmentCancellationResult,
    AppointmentNotCancelableError,
    AppointmentNotFoundError,
)
from app.domain.audit.enums import AuditActorType
from app.domain.patient_identity_resolution import (
    PatientIdentityResolutionRequest,
    PatientIdentityResolutionResult,
)
from app.domain.receptionist.enums import ReceptionistResponseType, ReceptionistTemplateType
from app.domain.retell_tools import (
    MissingProviderCallIdError,
    ParsedRetellToolCall,
    RetellSupportedToolName,
    RetellToolCallValidationError,
    UnsupportedRetellToolNameError,
    build_failed_tool_call_response,
    build_rejected_tool_call_response,
    build_retell_tool_call_event_type,
    build_retell_tool_call_idempotency_key,
    build_succeeded_tool_call_response,
    deserialize_tool_call_outcome,
    parse_retell_tool_call,
    serialize_tool_call_outcome,
)
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_booking import (
    VoiceBookingConfirmationRequest,
    VoiceBookingExpiredHoldError,
    VoiceBookingHoldOwnershipError,
    VoiceBookingIdentityConfirmationRequiredError,
    VoiceBookingIdentityNotResolvedError,
    VoiceBookingMissingConfirmationError,
    VoiceBookingMissingContextError,
    VoiceBookingMissingHoldError,
    VoiceBookingMissingIdentityError,
    VoiceBookingPatientNotFoundError,
    VoiceBookingQuotaExceededError,
    VoiceBookingResolutionIdRequiredError,
    VoiceBookingTemporaryFailureError,
    is_book_appointment_executable,
)
from app.domain.voice_cancellation import (
    VOICE_CANCELLATION_SOURCE,
    build_cancel_appointment_success_context_updates,
    is_cancel_appointment_executable,
    is_cancel_appointment_reference_ambiguous,
    resolve_cancel_appointment_id,
    validate_cancel_appointment_conversation_context,
)
from app.domain.voice_conversation import (
    ConversationNotFoundForBridgeError,
    VoiceCallNotFoundForBridgeError,
    VoiceConversationLinkConflictError,
    merge_check_availability_identity_fields,
    read_voice_context,
)
from app.domain.voice_rescheduling import (
    VOICE_RESCHEDULING_SOURCE,
    is_reschedule_appointment_executable,
    is_reschedule_appointment_reference_ambiguous,
    resolve_reschedule_original_appointment_id,
    resolve_reschedule_target_reference,
    validate_reschedule_appointment_conversation_context,
)
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import (
    BookAppointmentToolArguments,
    CancelAppointmentToolArguments,
    CheckAvailabilityToolArguments,
    ConfirmPatientIdentityToolArguments,
    HoldAppointmentSlotToolArguments,
    ReleaseAppointmentHoldToolArguments,
    RescheduleAppointmentToolArguments,
    ResolvePatientIdentityToolArguments,
    RetellCheckAvailabilityRequest,
    RetellToolCallRequest,
    RetellToolCallResponse,
    RetellToolResponse,
)
from app.schemas.scheduling import AppointmentResponse
from app.services.appointment_holds import (
    AppointmentHoldOwnershipError,
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)
from app.services.clinic_time import ClinicTimeService
from app.services.conversations import ConversationNotFoundError
from app.services.patient_identity_resolution import (
    PatientIdentityIncompleteError,
    PatientIdentityResolutionService,
    PatientResolutionNotFoundError,
)
from app.services.receptionist_response_planning import build_suggested_retell_response_text
from app.services.retell_call_lifecycle import DEFAULT_RETELL_PROVIDER
from app.services.retell_tool_registry import is_side_effecting_retell_tool
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    PatientLookupCriteria,
)
from app.services.scheduling_availability import SchedulingAvailabilityResolver
from app.services.voice_conversation_bridge import VoiceConversationBridgeService

logger = logging.getLogger("app.retell_tool_adapter")

_RETELL_TOOL_EXECUTION_FAILED_CODE = "retell_tool_execution_failed"
_PATIENT_RESOLUTION_NOT_FOUND_CODE = "patient_resolution_not_found"
_PATIENT_IDENTITY_CONFIRMATION_REJECTED_CODE = "patient_identity_confirmation_rejected"
_PATIENT_IDENTITY_RESOLUTION_UNAVAILABLE_CODE = "patient_identity_resolution_unavailable"

_READ_ONLY_VOICE_CONTEXT_RECOVERABLE_ERRORS = (
    VoiceCallNotFoundForBridgeError,
    ConversationNotFoundForBridgeError,
    VoiceConversationLinkConflictError,
)

_CHECK_AVAILABILITY_CONTEXT_UPDATE_RECOVERABLE_ERRORS = (
    ConversationNotFoundError,
    ConversationNotFoundForBridgeError,
)


class SchedulingServiceForRetellToolCalling(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        raise NotImplementedError

    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
        raise NotImplementedError

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        raise NotImplementedError

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        raise NotImplementedError


class VoiceCallRepositoryForRetellToolCalling(Protocol):
    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        raise NotImplementedError

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        raise NotImplementedError

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

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
        raise NotImplementedError


class AppointmentHoldServiceForRetellToolCalling(Protocol):
    ttl_seconds: int

    def create_hold(
        self,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        raise NotImplementedError

    def release_hold_by_id(self, *, hold_id: UUID, owner_id: str) -> None:
        raise NotImplementedError


class ConversationServiceForRetellToolCalling(Protocol):
    def merge_voice_context(
        self,
        *,
        conversation_id: UUID,
        voice_context: dict[str, Any],
    ) -> Conversation:
        raise NotImplementedError

    def clear_voice_active_hold(
        self,
        *,
        conversation_id: UUID,
    ) -> Conversation:
        raise NotImplementedError


class VoiceBookingConfirmationServiceForRetellToolCalling(Protocol):
    def confirm_and_book(
        self,
        request: VoiceBookingConfirmationRequest,
    ) -> Any:
        raise NotImplementedError


VoiceBookingConfirmationForRetell = VoiceBookingConfirmationServiceForRetellToolCalling


class AppointmentCancellationServiceForRetellToolCalling(Protocol):
    def cancel_appointment(
        self,
        request: AppointmentCancellationRequest,
    ) -> AppointmentCancellationResult:
        raise NotImplementedError


AppointmentCancellationForRetell = AppointmentCancellationServiceForRetellToolCalling


class AppointmentReschedulingServiceForRetellToolCalling(Protocol):
    def reschedule_appointment(
        self,
        request: AppointmentReschedulingRequest,
    ) -> AppointmentReschedulingResult:
        raise NotImplementedError


AppointmentReschedulingForRetell = AppointmentReschedulingServiceForRetellToolCalling


class AppointmentRepositoryForRetellToolCalling(Protocol):
    def get_by_id(self, appointment_id: UUID) -> Appointment | None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _VoiceConversationSession:
    conversation: Conversation
    voice_context: dict[str, Any]


class RetellToolCallingAdapter:
    def __init__(
        self,
        *,
        scheduling_service: SchedulingServiceForRetellToolCalling,
        hold_service: AppointmentHoldServiceForRetellToolCalling,
        voice_calls: VoiceCallRepositoryForRetellToolCalling,
        scheduling_tools: RetellSchedulingToolAdapter | None = None,
        voice_conversation_bridge: VoiceConversationBridgeService | None = None,
        conversations: ConversationServiceForRetellToolCalling | None = None,
        voice_booking_confirmation: VoiceBookingConfirmationForRetell | None = None,
        appointment_cancellation: AppointmentCancellationForRetell | None = None,
        appointment_rescheduling: AppointmentReschedulingForRetell | None = None,
        appointments: AppointmentRepositoryForRetellToolCalling | None = None,
        clinic_time_service: ClinicTimeService | None = None,
        patient_identity_resolution: PatientIdentityResolutionService | None = None,
        provider: str = DEFAULT_RETELL_PROVIDER,
    ) -> None:
        self.scheduling_service = scheduling_service
        self.hold_service = hold_service
        self.voice_calls = voice_calls
        self.scheduling_tools = scheduling_tools or RetellSchedulingToolAdapter(
            scheduling_service,
        )
        self.voice_conversation_bridge = voice_conversation_bridge
        self.conversations = conversations
        self.voice_booking_confirmation = voice_booking_confirmation
        self.appointment_cancellation = appointment_cancellation
        self.appointment_rescheduling = appointment_rescheduling
        self.appointments = appointments
        self.clinic_time_service = clinic_time_service
        self.patient_identity_resolution = patient_identity_resolution
        self.provider = provider

    def execute(self, request: RetellToolCallRequest) -> RetellToolCallResponse:
        try:
            parsed = parse_retell_tool_call(request)
        except MissingProviderCallIdError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=RETELL_TOOL_PROVIDER_CALL_ID_REQUIRED_CODE,
            )
        except UnsupportedRetellToolNameError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=UNSUPPORTED_RETELL_TOOL_CODE,
            )
        except RetellToolCallValidationError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=RETELL_TOOL_ARGUMENTS_INVALID_CODE,
            )
        except Exception:
            logger.exception("Unexpected Retell tool call validation failure")
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=INVALID_RETELL_PAYLOAD_CODE,
            )

        duplicate_response = self._load_duplicate_side_effect_response(parsed)

        if duplicate_response is not None:
            return duplicate_response

        try:
            response = self._dispatch(parsed)
        except Exception:
            logger.exception(
                "Unexpected Retell tool execution failure for tool %s",
                parsed.tool_name.value,
            )
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_RETELL_TOOL_EXECUTION_FAILED_CODE,
            )

        if (
            response.status == "succeeded"
            and parsed.tool_call_id is not None
            and is_side_effecting_retell_tool(parsed.tool_name)
        ):
            self._record_side_effect_outcome(parsed, response)

        return response

    def _dispatch(self, parsed: ParsedRetellToolCall) -> RetellToolCallResponse:
        if parsed.tool_name is RetellSupportedToolName.GET_CLINIC_CONTEXT:
            return self._execute_get_clinic_context(parsed)

        if parsed.tool_name is RetellSupportedToolName.CHECK_AVAILABILITY:
            return self._execute_check_availability(parsed)

        if parsed.tool_name is RetellSupportedToolName.HOLD_APPOINTMENT_SLOT:
            return self._execute_hold_appointment_slot(parsed)

        if parsed.tool_name is RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD:
            return self._execute_release_appointment_hold(parsed)

        if parsed.tool_name is RetellSupportedToolName.BOOK_APPOINTMENT:
            return self._execute_book_appointment(parsed)

        if parsed.tool_name is RetellSupportedToolName.CANCEL_APPOINTMENT:
            return self._execute_cancel_appointment(parsed)

        if parsed.tool_name is RetellSupportedToolName.RESCHEDULE_APPOINTMENT:
            return self._execute_reschedule_appointment(parsed)

        if parsed.tool_name is RetellSupportedToolName.RESOLVE_PATIENT_IDENTITY:
            return self._execute_resolve_patient_identity(parsed)

        if parsed.tool_name is RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY:
            return self._execute_confirm_patient_identity(parsed)

        return build_rejected_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code=UNSUPPORTED_RETELL_TOOL_CODE,
        )

    def _execute_get_clinic_context(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.clinic_time_service is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="clinic_time_unavailable",
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self.clinic_time_service.get_current_clinic_context().to_tool_result(),
        )

    def _execute_check_availability(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        arguments = _as_check_availability_arguments(parsed.arguments)
        voice_session = self._ensure_voice_conversation_for_read_only_tool(parsed)
        voice_context = voice_session.voice_context if voice_session is not None else {}

        if self.clinic_time_service is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="clinic_time_unavailable",
            )

        availability_resolver = SchedulingAvailabilityResolver(self.clinic_time_service)
        availability_window = availability_resolver.resolve_check_availability(
            arguments,
            voice_context,
        )
        if not availability_window.is_resolved:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=availability_window.error_code or "invalid_scheduling_expression",
            )

        resolved_arguments = merge_check_availability_identity_fields(
            arguments.model_copy(
                update={
                    "start_from": availability_window.start_from,
                    "start_to": availability_window.end_to,
                },
            ),
            voice_context,
        )
        if resolved_arguments is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=RETELL_TOOL_ARGUMENTS_INVALID_CODE,
            )

        payload = RetellCheckAvailabilityRequest(
            call_id=parsed.provider_call_id,
            doctor_id=resolved_arguments.doctor_id,
            doctor_name=resolved_arguments.doctor_name,
            specialty_name=resolved_arguments.specialty_name,
            start_from=resolved_arguments.start_from,
            start_to=resolved_arguments.start_to,
        )
        legacy_response = self.scheduling_tools.check_availability(payload)

        response = self._translate_legacy_response(parsed, legacy_response)

        if response.status != "succeeded":
            return response

        result = dict(response.result)
        slots = result.get("available_slots")

        if isinstance(slots, list) and resolved_arguments.limit is not None:
            result["available_slots"] = slots[: resolved_arguments.limit]

        if voice_session is not None:
            self._try_update_voice_context_after_check_availability(
                parsed=parsed,
                conversation_id=voice_session.conversation.id,
                arguments=resolved_arguments,
                result=result,
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=result,
        )

    def _execute_hold_appointment_slot(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        voice_session = self._ensure_voice_conversation(parsed)
        arguments = _as_hold_arguments(parsed.arguments)
        owner_id = self._resolve_hold_owner_id(
            provider_call_id=parsed.provider_call_id,
            owner_id=arguments.owner_id,
            conversation_id=(voice_session.conversation.id if voice_session is not None else None),
        )

        if owner_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="missing_hold_owner",
            )

        try:
            slot = self.scheduling_service.get_available_slot_for_hold(
                arguments.availability_slot_id,
            )
            if self.clinic_time_service is not None:
                slot_validation = SchedulingAvailabilityResolver(
                    self.clinic_time_service,
                ).validate_slot_start_time(slot.start_time)
                if not slot_validation.is_valid:
                    return build_failed_tool_call_response(
                        tool_name=parsed.tool_name.value,
                        tool_call_id=parsed.tool_call_id,
                        error_code=slot_validation.error_code or "invalid_scheduling_expression",
                    )
            hold = self.hold_service.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
            )
        except AvailabilitySlotNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="availability_slot_not_found",
            )
        except AvailabilitySlotUnavailableError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="availability_slot_unavailable",
            )
        except AppointmentSlotAlreadyHeldError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="slot_already_held",
            )
        except (
            InvalidAppointmentHoldOwnerError,
            InvalidAppointmentHoldWindowError,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="invalid_appointment_hold",
            )
        except AppointmentHoldOwnershipError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_hold_ownership_error",
            )

        if voice_session is not None:
            self._update_voice_context_after_hold(
                conversation_id=voice_session.conversation.id,
                hold=hold,
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._with_suggested_response_text(
                {
                    "hold_id": str(hold.hold_id),
                    "availability_slot_id": str(hold.availability_slot_id),
                    "doctor_id": str(hold.doctor_id),
                    "start_time": hold.start_time.isoformat(),
                    "end_time": hold.end_time.isoformat(),
                    "expires_in_seconds": self._resolve_hold_ttl_seconds(arguments.ttl_seconds),
                },
                template_type=ReceptionistTemplateType.SLOT_HOLD_CREATED,
                facts={},
                fallback_text=(
                    "I can hold that time while I get your details. I'll need your name, "
                    "date of birth, and email before I can book it."
                ),
                response_type=ReceptionistResponseType.SCHEDULING,
            ),
        )

    def _execute_release_appointment_hold(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        voice_session = self._ensure_voice_conversation(parsed)
        arguments = _as_release_arguments(parsed.arguments)
        owner_id = self._resolve_hold_owner_id(
            provider_call_id=parsed.provider_call_id,
            owner_id=arguments.owner_id,
            conversation_id=(voice_session.conversation.id if voice_session is not None else None),
        )

        if owner_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="missing_hold_owner",
            )

        try:
            self.hold_service.release_hold_by_id(
                hold_id=arguments.hold_id,
                owner_id=owner_id,
            )
        except AppointmentHoldOwnershipError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_hold_ownership_error",
            )

        if voice_session is not None:
            self._clear_voice_active_hold_context(conversation_id=voice_session.conversation.id)

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result={"released": True, "hold_id": str(arguments.hold_id)},
        )

    def _execute_book_appointment(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.voice_booking_confirmation is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="voice_booking_unavailable",
            )

        arguments = _as_book_appointment_arguments(parsed.arguments)

        if not is_book_appointment_executable(arguments):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="booking_confirmation_required",
            )

        voice_session = self._ensure_voice_conversation(parsed)
        if voice_session is None:
            return self._missing_voice_conversation_context_response(parsed)

        voice_call = self.voice_calls.get_by_provider_call_id(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
        )
        if voice_call is None:
            return self._missing_voice_conversation_context_response(parsed)

        idempotency_key = self._build_book_appointment_idempotency_key(parsed)

        try:
            booking_result = self.voice_booking_confirmation.confirm_and_book(
                VoiceBookingConfirmationRequest(
                    provider=self.provider,
                    provider_call_id=parsed.provider_call_id,
                    tool_call_id=parsed.tool_call_id,
                    voice_call_id=voice_call.id,
                    conversation_id=voice_session.conversation.id,
                    hold_id=arguments.hold_id,
                    slot_id=arguments.slot_id,
                    patient_name=arguments.patient_name,
                    patient_date_of_birth=arguments.patient_date_of_birth,
                    patient_email=arguments.patient_email,
                    patient_phone=arguments.patient_phone,
                    patient_resolution_id=arguments.patient_resolution_id,
                    explicit_confirmation=arguments.explicit_confirmation,
                    confirmation_text=arguments.confirmation_text,
                    notes=arguments.notes,
                    idempotency_key=idempotency_key,
                ),
            )
        except VoiceBookingMissingConfirmationError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="booking_confirmation_required",
            )
        except VoiceBookingMissingIdentityError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="booking_identity_missing",
            )
        except VoiceBookingMissingHoldError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="booking_hold_missing",
            )
        except VoiceBookingExpiredHoldError:
            return self._build_failed_with_suggested_response(
                parsed,
                error_code="appointment_hold_expired",
                template_type=ReceptionistTemplateType.BOOKING_FAILED,
                facts={"failure_code": "appointment_hold_expired"},
                fallback_text=(
                    "That time may no longer be available. "
                    "Let me check the latest schedule again."
                ),
                response_type=ReceptionistResponseType.CONFIRMATION,
            )
        except VoiceBookingHoldOwnershipError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_hold_owner_mismatch",
            )
        except VoiceBookingPatientNotFoundError:
            return self._build_failed_with_suggested_response(
                parsed,
                error_code="patient_not_found",
                template_type=ReceptionistTemplateType.BOOKING_FAILED,
                facts={"failure_code": "patient_not_found"},
                fallback_text=(
                    "I'm not matching those details yet — could we try your name "
                    "and date of birth once more?"
                ),
                response_type=ReceptionistResponseType.CONFIRMATION,
            )
        except VoiceBookingIdentityConfirmationRequiredError:
            return self._build_failed_with_suggested_response(
                parsed,
                error_code="patient_identity_confirmation_required",
                template_type=ReceptionistTemplateType.BOOKING_FAILED,
                facts={"failure_code": "patient_identity_confirmation_required"},
                fallback_text=(
                    "Before I can book this appointment, I need to confirm your identity. "
                    "Is the patient record I found correct?"
                ),
                response_type=ReceptionistResponseType.CONFIRMATION,
            )
        except VoiceBookingResolutionIdRequiredError:
            return self._build_failed_with_suggested_response(
                parsed,
                error_code="patient_resolution_id_required",
                template_type=ReceptionistTemplateType.BOOKING_FAILED,
                facts={"failure_code": "patient_resolution_id_required"},
                fallback_text=(
                    "I need the patient resolution from this call before I can book. "
                    "Let me use the identity we already confirmed."
                ),
                response_type=ReceptionistResponseType.CONFIRMATION,
            )
        except VoiceBookingIdentityNotResolvedError:
            return self._build_failed_with_suggested_response(
                parsed,
                error_code="patient_identity_not_resolved",
                template_type=ReceptionistTemplateType.BOOKING_FAILED,
                facts={"failure_code": "patient_identity_not_resolved"},
                fallback_text=(
                    "I need to verify your patient details again before booking. "
                    "Let's confirm your name and date of birth."
                ),
                response_type=ReceptionistResponseType.CONFIRMATION,
            )
        except VoiceBookingQuotaExceededError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="demo_guardrail_limit_exceeded",
            )
        except VoiceBookingMissingContextError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="missing_voice_conversation_context",
            )
        except VoiceBookingTemporaryFailureError as exc:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=exc.error_code,
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._build_book_appointment_result(booking_result),
            duplicate=booking_result.duplicate,
        )

    def _execute_cancel_appointment(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.appointment_cancellation is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="voice_cancellation_unavailable",
            )

        arguments = _as_cancel_appointment_arguments(parsed.arguments)

        if not arguments.explicit_confirmation:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="cancellation_confirmation_required",
            )

        voice_session = self._ensure_voice_conversation(parsed)
        if voice_session is None:
            return self._missing_voice_conversation_context_response(parsed)

        conversation = voice_session.conversation
        voice_context = voice_session.voice_context

        if is_cancel_appointment_reference_ambiguous(
            arguments,
            voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        appointment_id = resolve_cancel_appointment_id(
            arguments,
            voice_context,
            conversation_appointment_id=conversation.appointment_id,
        )
        if appointment_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        if not validate_cancel_appointment_conversation_context(
            appointment_id,
            arguments=arguments,
            voice_context=voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_context_mismatch",
            )

        if not is_cancel_appointment_executable(
            arguments,
            voice_context=voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        idempotency_key = self._build_cancel_appointment_idempotency_key(parsed)

        try:
            cancellation_result = self.appointment_cancellation.cancel_appointment(
                AppointmentCancellationRequest(
                    appointment_id=appointment_id,
                    explicit_confirmation=arguments.explicit_confirmation,
                    idempotency_key=idempotency_key,
                    cancellation_reason=arguments.cancellation_reason,
                    source=VOICE_CANCELLATION_SOURCE,
                    actor_type=AuditActorType.RETELL,
                    actor_id=parsed.provider_call_id,
                    call_id=parsed.provider_call_id,
                    conversation_id=str(conversation.id),
                ),
            )
        except AppointmentCancellationMissingConfirmationError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="cancellation_confirmation_required",
            )
        except AppointmentNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_not_found",
            )
        except AppointmentNotCancelableError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_not_cancelable",
            )

        self._update_voice_context_after_cancellation(
            conversation_id=conversation.id,
            appointment_id=cancellation_result.appointment_id,
        )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._build_cancel_appointment_result(cancellation_result),
            duplicate=cancellation_result.duplicate,
        )

    def _execute_reschedule_appointment(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.appointment_rescheduling is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="voice_rescheduling_unavailable",
            )

        arguments = _as_reschedule_appointment_arguments(parsed.arguments)

        if not arguments.explicit_confirmation:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="reschedule_confirmation_required",
            )

        voice_session = self._ensure_voice_conversation(parsed)
        if voice_session is None:
            return self._missing_voice_conversation_context_response(parsed)

        conversation = voice_session.conversation
        voice_context = voice_session.voice_context

        if is_reschedule_appointment_reference_ambiguous(
            arguments,
            voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        appointment_id = resolve_reschedule_original_appointment_id(
            arguments,
            voice_context,
            conversation_appointment_id=conversation.appointment_id,
        )
        if appointment_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        if not validate_reschedule_appointment_conversation_context(
            appointment_id,
            arguments=arguments,
            voice_context=voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_context_mismatch",
            )

        if not is_reschedule_appointment_executable(
            arguments,
            voice_context=voice_context,
            conversation_appointment_id=conversation.appointment_id,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_reference_required",
            )

        hold_id, new_slot_id, target_error = resolve_reschedule_target_reference(arguments)
        if target_error is not None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=target_error,
            )

        if new_slot_id is not None and self.clinic_time_service is not None:
            try:
                slot = self.scheduling_service.get_available_slot_for_hold(new_slot_id)
            except AvailabilitySlotNotFoundError:
                return build_failed_tool_call_response(
                    tool_name=parsed.tool_name.value,
                    tool_call_id=parsed.tool_call_id,
                    error_code="availability_slot_not_found",
                )
            except AvailabilitySlotUnavailableError:
                return build_failed_tool_call_response(
                    tool_name=parsed.tool_name.value,
                    tool_call_id=parsed.tool_call_id,
                    error_code="availability_slot_unavailable",
                )

            slot_validation = SchedulingAvailabilityResolver(
                self.clinic_time_service,
            ).validate_slot_start_time(slot.start_time)
            if not slot_validation.is_valid:
                return build_failed_tool_call_response(
                    tool_name=parsed.tool_name.value,
                    tool_call_id=parsed.tool_call_id,
                    error_code=slot_validation.error_code or "invalid_scheduling_expression",
                )

        owner_id: str | None = None
        if hold_id is not None:
            owner_id = self._resolve_hold_owner_id(
                provider_call_id=parsed.provider_call_id,
                owner_id=None,
                conversation_id=conversation.id,
            )

        idempotency_key = self._build_reschedule_appointment_idempotency_key(parsed)

        try:
            reschedule_result = self.appointment_rescheduling.reschedule_appointment(
                AppointmentReschedulingRequest(
                    appointment_id=appointment_id,
                    hold_id=hold_id,
                    new_slot_id=new_slot_id,
                    explicit_confirmation=arguments.explicit_confirmation,
                    idempotency_key=idempotency_key,
                    owner_id=owner_id,
                    rescheduling_reason=normalize_rescheduling_reason(
                        arguments.reschedule_reason,
                    ),
                    source=VOICE_RESCHEDULING_SOURCE,
                    actor_type=AuditActorType.RETELL,
                    actor_id=parsed.provider_call_id,
                    call_id=parsed.provider_call_id,
                    conversation_id=str(conversation.id),
                ),
            )
        except AppointmentReschedulingMissingConfirmationError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="reschedule_confirmation_required",
            )
        except AppointmentReschedulingNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_not_found",
            )
        except AppointmentReschedulingNotReschedulableError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_not_reschedulable",
            )
        except AppointmentReschedulingHoldExpiredError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="active_hold_required",
            )
        except AppointmentReschedulingSlotNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="new_slot_required",
            )
        except AppointmentReschedulingSlotUnavailableError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="slot_unavailable",
            )
        except AppointmentReschedulingSlotAlreadyBookedError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="slot_already_booked",
            )
        except AppointmentReschedulingError as exc:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=exc.failure_code.value,
            )

        self._update_voice_context_after_reschedule(
            conversation_id=conversation.id,
            reschedule_result=reschedule_result,
        )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._build_reschedule_appointment_result(reschedule_result),
            duplicate=reschedule_result.duplicate,
        )

    def _update_voice_context_after_reschedule(
        self,
        *,
        conversation_id: UUID,
        reschedule_result: AppointmentReschedulingResult,
    ) -> None:
        if self.voice_conversation_bridge is None or self.appointments is None:
            return

        new_appointment = self.appointments.get_by_id(reschedule_result.new_appointment_id)
        if new_appointment is None or new_appointment.availability_slot_id is None:
            return

        self.voice_conversation_bridge.record_successful_reschedule_context(
            conversation_id=conversation_id,
            original_appointment_id=reschedule_result.original_appointment_id,
            new_appointment_id=reschedule_result.new_appointment_id,
            availability_slot_id=new_appointment.availability_slot_id,
            start_time=new_appointment.start_time.isoformat(),
            end_time=new_appointment.end_time.isoformat(),
            duplicate=reschedule_result.duplicate,
        )

    def _build_cancel_appointment_idempotency_key(
        self,
        parsed: ParsedRetellToolCall,
    ) -> str:
        if parsed.tool_call_id is not None:
            return build_retell_tool_call_idempotency_key(
                provider=self.provider,
                provider_call_id=parsed.provider_call_id,
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
            )

        return f"{self.provider}:{parsed.provider_call_id}:cancel_appointment"

    def _build_reschedule_appointment_idempotency_key(
        self,
        parsed: ParsedRetellToolCall,
    ) -> str:
        if parsed.tool_call_id is not None:
            return build_retell_tool_call_idempotency_key(
                provider=self.provider,
                provider_call_id=parsed.provider_call_id,
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
            )

        return f"{self.provider}:{parsed.provider_call_id}:reschedule_appointment"

    def _build_reschedule_appointment_result(
        self,
        reschedule_result: AppointmentReschedulingResult,
    ) -> dict[str, Any]:
        appointment_summary: dict[str, Any] = {
            "appointment_id": str(reschedule_result.new_appointment_id),
            "patient_id": str(reschedule_result.patient_id),
            "status": AppointmentStatus.SCHEDULED.value,
        }

        if self.appointments is not None:
            appointment = self.appointments.get_by_id(reschedule_result.new_appointment_id)
            if appointment is not None:
                appointment_summary = AppointmentResponse.model_validate(
                    appointment,
                ).model_dump(mode="json")

        return self._with_suggested_response_text(
            {
                "original_appointment_id": str(reschedule_result.original_appointment_id),
                "new_appointment_id": str(reschedule_result.new_appointment_id),
                "status": AppointmentStatus.SCHEDULED.value,
                "already_rescheduled": reschedule_result.already_rescheduled,
                "appointment": appointment_summary,
                "email_confirmation_queued": reschedule_result.confirmation_email_created,
            },
            template_type=ReceptionistTemplateType.RESCHEDULE_SUCCEEDED,
            facts=self._build_appointment_response_facts(appointment_summary),
            fallback_text=(
                "Your appointment has been rescheduled. "
                "You'll receive a confirmation email shortly."
            ),
            response_type=ReceptionistResponseType.CONFIRMATION,
        )

    def _execute_resolve_patient_identity(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.patient_identity_resolution is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_PATIENT_IDENTITY_RESOLUTION_UNAVAILABLE_CODE,
            )

        arguments = _as_resolve_patient_identity_arguments(parsed.arguments)
        voice_session = self._ensure_voice_conversation_for_read_only_tool(parsed)

        try:
            resolution_result = self.patient_identity_resolution.resolve(
                PatientIdentityResolutionRequest(
                    patient_name=arguments.patient_name,
                    patient_date_of_birth=arguments.patient_date_of_birth,
                    provider_call_id=parsed.provider_call_id,
                    conversation_id=(
                        voice_session.conversation.id if voice_session is not None else None
                    ),
                    patient_email=arguments.patient_email,
                    patient_phone=arguments.patient_phone,
                    caller_claims_existing_patient=arguments.caller_claims_existing_patient,
                    allow_demo_patient_creation=arguments.allow_demo_patient_creation,
                ),
            )
        except PatientIdentityIncompleteError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="booking_identity_missing",
            )

        self._merge_patient_resolution_voice_context(
            voice_session=voice_session,
            patient_resolution_id=resolution_result.patient_resolution_id,
        )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._build_patient_identity_resolution_result(resolution_result),
        )

    def _execute_confirm_patient_identity(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        if self.patient_identity_resolution is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_PATIENT_IDENTITY_RESOLUTION_UNAVAILABLE_CODE,
            )

        arguments = _as_confirm_patient_identity_arguments(parsed.arguments)
        voice_session = self._ensure_voice_conversation_for_read_only_tool(parsed)
        conversation_id = voice_session.conversation.id if voice_session is not None else None

        if not arguments.confirmed:
            try:
                rejection_result = self.patient_identity_resolution.reject_resolution(
                    patient_resolution_id=arguments.patient_resolution_id,
                    provider_call_id=parsed.provider_call_id,
                    conversation_id=conversation_id,
                )
            except PatientResolutionNotFoundError:
                return build_failed_tool_call_response(
                    tool_name=parsed.tool_name.value,
                    tool_call_id=parsed.tool_call_id,
                    error_code=_PATIENT_RESOLUTION_NOT_FOUND_CODE,
                )

            return build_rejected_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_PATIENT_IDENTITY_CONFIRMATION_REJECTED_CODE,
                result=self._build_patient_identity_resolution_result(rejection_result),
            )

        try:
            resolution_result = self.patient_identity_resolution.confirm_resolution(
                patient_resolution_id=arguments.patient_resolution_id,
                provider_call_id=parsed.provider_call_id,
                conversation_id=conversation_id,
            )
        except PatientResolutionNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_PATIENT_RESOLUTION_NOT_FOUND_CODE,
            )

        self._merge_patient_resolution_voice_context(
            voice_session=voice_session,
            patient_resolution_id=resolution_result.patient_resolution_id,
        )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=self._build_patient_identity_resolution_result(
                resolution_result,
                confirmed=True,
            ),
        )

    def _merge_patient_resolution_voice_context(
        self,
        *,
        voice_session: _VoiceConversationSession | None,
        patient_resolution_id: str | None,
    ) -> None:
        if voice_session is None or self.conversations is None:
            return

        if patient_resolution_id is None:
            self.conversations.merge_voice_context(
                conversation_id=voice_session.conversation.id,
                voice_context={"patient_resolution_id": None},
            )
            return

        self.conversations.merge_voice_context(
            conversation_id=voice_session.conversation.id,
            voice_context={"patient_resolution_id": patient_resolution_id},
        )

    def _build_patient_identity_resolution_result(
        self,
        resolution_result: PatientIdentityResolutionResult,
        *,
        confirmed: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "match_status": resolution_result.match_status.value,
            "requires_confirmation": resolution_result.requires_confirmation,
            "display_name": resolution_result.display_name,
            "candidate_display_name": resolution_result.candidate_display_name,
            "confirmation_question": resolution_result.confirmation_question,
            "patient_resolution_id": resolution_result.patient_resolution_id,
            "next_step": resolution_result.next_step.value,
            "suggested_response_text": resolution_result.suggested_response_text,
        }
        if confirmed is not None:
            payload["confirmed"] = confirmed

        return payload

    def _build_cancel_appointment_result(
        self,
        cancellation_result: AppointmentCancellationResult,
    ) -> dict[str, Any]:
        appointment_summary: dict[str, Any] = {
            "appointment_id": str(cancellation_result.appointment_id),
            "patient_id": str(cancellation_result.patient_id),
            "status": AppointmentStatus.CANCELLED.value,
        }

        if self.appointments is not None:
            appointment = self.appointments.get_by_id(cancellation_result.appointment_id)
            if appointment is not None:
                appointment_summary = AppointmentResponse.model_validate(
                    appointment,
                ).model_dump(mode="json")

        return self._with_suggested_response_text(
            {
                "appointment_id": str(cancellation_result.appointment_id),
                "status": AppointmentStatus.CANCELLED.value,
                "already_cancelled": cancellation_result.already_cancelled,
                "appointment": appointment_summary,
            },
            template_type=ReceptionistTemplateType.CANCELLATION_SUCCEEDED,
            facts={},
            fallback_text="Your appointment has been cancelled.",
            response_type=ReceptionistResponseType.CONFIRMATION,
        )

    def _update_voice_context_after_cancellation(
        self,
        *,
        conversation_id: UUID,
        appointment_id: UUID,
    ) -> None:
        if self.conversations is None:
            return

        self.conversations.clear_voice_active_hold(conversation_id=conversation_id)
        self.conversations.merge_voice_context(
            conversation_id=conversation_id,
            voice_context=build_cancel_appointment_success_context_updates(
                appointment_id=appointment_id,
            ),
        )

    def _build_book_appointment_idempotency_key(
        self,
        parsed: ParsedRetellToolCall,
    ) -> str:
        if parsed.tool_call_id is not None:
            return build_retell_tool_call_idempotency_key(
                provider=self.provider,
                provider_call_id=parsed.provider_call_id,
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
            )

        return f"{self.provider}:{parsed.provider_call_id}:book_appointment"

    def _build_book_appointment_result(self, booking_result: Any) -> dict[str, Any]:
        appointment_summary: dict[str, Any] = {
            "appointment_id": str(booking_result.appointment_id),
            "patient_id": str(booking_result.patient_id),
            "availability_slot_id": str(booking_result.availability_slot_id),
            "hold_id": booking_result.hold_id,
        }

        if self.appointments is not None:
            appointment = self.appointments.get_by_id(booking_result.appointment_id)
            if appointment is not None:
                appointment_summary = AppointmentResponse.model_validate(
                    appointment,
                ).model_dump(mode="json")

        return self._with_suggested_response_text(
            {
                "appointment_id": str(booking_result.appointment_id),
                "status": "scheduled",
                "appointment": appointment_summary,
                "email_confirmation_queued": booking_result.confirmation_email_created,
            },
            template_type=ReceptionistTemplateType.BOOKING_SUCCEEDED,
            facts=self._build_appointment_response_facts(appointment_summary),
            fallback_text=(
                "You're all set. Your appointment is confirmed. "
                "You'll receive a confirmation email shortly. "
                "Is there anything else you need today?"
            ),
            response_type=ReceptionistResponseType.CONFIRMATION,
        )

    def _build_failed_with_suggested_response(
        self,
        parsed: ParsedRetellToolCall,
        *,
        error_code: str,
        template_type: ReceptionistTemplateType,
        facts: dict[str, Any],
        fallback_text: str,
        response_type: ReceptionistResponseType,
    ) -> RetellToolCallResponse:
        return build_failed_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code=error_code,
            result=self._with_suggested_response_text(
                {},
                template_type=template_type,
                facts=facts,
                fallback_text=fallback_text,
                response_type=response_type,
            ),
        )

    def _build_appointment_response_facts(
        self,
        appointment_summary: dict[str, Any],
    ) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        appointment_id = appointment_summary.get("id") or appointment_summary.get("appointment_id")
        if appointment_id is not None:
            facts["appointment_id"] = str(appointment_id)

        start_time_raw = appointment_summary.get("start_time")
        if start_time_raw is not None:
            try:
                parsed_start = datetime.fromisoformat(str(start_time_raw).replace("Z", "+00:00"))
            except ValueError:
                parsed_start = None
            if parsed_start is not None:
                facts["appointment_time"] = self._format_voice_appointment_time(parsed_start)

        doctor_id = appointment_summary.get("doctor_id")
        if doctor_id is not None:
            doctor_name = self._resolve_doctor_display_name(UUID(str(doctor_id)))
            if doctor_name is not None:
                facts["doctor_name"] = doctor_name

        return facts

    def _format_voice_appointment_time(self, start_time: datetime) -> str:
        if self.clinic_time_service is not None:
            localized = start_time.astimezone(self.clinic_time_service.timezone)
            date_part = f"{localized.strftime('%A, %B')} {localized.day}"
            time_part = localized.strftime("%I:%M %p").lstrip("0")
            return f"{date_part} at {time_part}"

        return start_time.strftime("%Y-%m-%d %H:%M")

    def _resolve_doctor_display_name(self, doctor_id: UUID) -> str | None:
        for doctor in self.scheduling_service.list_doctors():
            if doctor.id == doctor_id:
                return doctor.full_name
        return None

    def _with_suggested_response_text(
        self,
        result: dict[str, Any],
        *,
        template_type: ReceptionistTemplateType,
        facts: dict[str, Any],
        fallback_text: str,
        response_type: ReceptionistResponseType,
    ) -> dict[str, Any]:
        return {
            **result,
            "suggested_response_text": build_suggested_retell_response_text(
                template_type=template_type,
                facts=facts,
                fallback_text=fallback_text,
                response_type=response_type,
            ),
        }

    def _ensure_voice_conversation(
        self,
        parsed: ParsedRetellToolCall,
    ) -> _VoiceConversationSession | None:
        if self.voice_conversation_bridge is None:
            return None

        try:
            conversation = self.voice_conversation_bridge.ensure_conversation_for_tool_callback(
                self.provider,
                parsed.provider_call_id,
            )
        except (VoiceCallNotFoundForBridgeError, ConversationNotFoundForBridgeError):
            self._log_missing_voice_conversation_context(parsed)
            return None

        return _VoiceConversationSession(
            conversation=conversation,
            voice_context=read_voice_context(conversation.conversation_metadata),
        )

    def _ensure_voice_conversation_for_read_only_tool(
        self,
        parsed: ParsedRetellToolCall,
    ) -> _VoiceConversationSession | None:
        if self.voice_conversation_bridge is None:
            return None

        try:
            conversation = self.voice_conversation_bridge.ensure_conversation_for_tool_callback(
                self.provider,
                parsed.provider_call_id,
            )
        except _READ_ONLY_VOICE_CONTEXT_RECOVERABLE_ERRORS as exc:
            self._log_recoverable_check_availability_voice_context_failure(
                parsed,
                failure_stage="ensure",
                failure_type=type(exc).__name__,
            )
            return None

        return _VoiceConversationSession(
            conversation=conversation,
            voice_context=read_voice_context(conversation.conversation_metadata),
        )

    def _try_update_voice_context_after_check_availability(
        self,
        *,
        parsed: ParsedRetellToolCall,
        conversation_id: UUID,
        arguments: CheckAvailabilityToolArguments,
        result: dict[str, Any],
    ) -> None:
        try:
            self._update_voice_context_after_check_availability(
                conversation_id=conversation_id,
                arguments=arguments,
                result=result,
            )
        except _CHECK_AVAILABILITY_CONTEXT_UPDATE_RECOVERABLE_ERRORS as exc:
            self._log_recoverable_check_availability_voice_context_failure(
                parsed,
                failure_stage="context_update",
                failure_type=type(exc).__name__,
            )

    def _log_recoverable_check_availability_voice_context_failure(
        self,
        parsed: ParsedRetellToolCall,
        *,
        failure_stage: str,
        failure_type: str,
    ) -> None:
        logger.warning(
            "retell_check_availability_voice_context_failure",
            extra={
                "event": "retell_check_availability_voice_context_failure",
                "failure_stage": failure_stage,
                "failure_type": failure_type,
                "provider_call_id": parsed.provider_call_id,
                "tool_name": parsed.tool_name.value,
                "tool_call_id": parsed.tool_call_id,
                "request_id": get_request_id(),
                "correlation_id": get_correlation_id(),
            },
        )

    def _missing_voice_conversation_context_response(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        self._log_missing_voice_conversation_context(parsed)
        return build_failed_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code="missing_voice_conversation_context",
        )

    def _log_missing_voice_conversation_context(
        self,
        parsed: ParsedRetellToolCall,
    ) -> None:
        logger.warning(
            "retell_voice_conversation_context_missing",
            extra={
                "event": "retell_voice_conversation_context_missing",
                "provider_call_id": parsed.provider_call_id,
                "tool_name": parsed.tool_name.value,
                "request_id": get_request_id(),
                "correlation_id": get_correlation_id(),
            },
        )

    def _update_voice_context_after_check_availability(
        self,
        *,
        conversation_id: UUID,
        arguments: CheckAvailabilityToolArguments,
        result: dict[str, Any],
    ) -> None:
        if self.conversations is None:
            return

        voice_context: dict[str, Any] = {}
        if arguments.specialty_name is not None:
            voice_context["specialty_name"] = arguments.specialty_name
        if arguments.doctor_name is not None:
            voice_context["doctor_name"] = arguments.doctor_name

        doctor_id = result.get("doctor_id")
        if doctor_id is not None:
            voice_context["doctor_id"] = str(doctor_id)

        if arguments.start_from is not None:
            voice_context["requested_date"] = arguments.start_from.date().isoformat()

        if not voice_context:
            return

        self.conversations.merge_voice_context(
            conversation_id=conversation_id,
            voice_context=voice_context,
        )

    def _update_voice_context_after_hold(
        self,
        *,
        conversation_id: UUID,
        hold: AppointmentHold,
    ) -> None:
        if self.conversations is None:
            return

        self.conversations.merge_voice_context(
            conversation_id=conversation_id,
            voice_context={
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(hold.availability_slot_id),
                "doctor_id": str(hold.doctor_id),
                "start_time": hold.start_time.isoformat(),
                "end_time": hold.end_time.isoformat(),
            },
        )

    def _clear_voice_active_hold_context(self, *, conversation_id: UUID) -> None:
        if self.conversations is None:
            return

        self.conversations.clear_voice_active_hold(conversation_id=conversation_id)

    def _translate_legacy_response(
        self,
        parsed: ParsedRetellToolCall,
        legacy_response: RetellToolResponse,
    ) -> RetellToolCallResponse:
        if legacy_response.ok:
            result = legacy_response.result

            if not isinstance(result, dict):
                result = {}

            return build_succeeded_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                result=result,
            )

        return build_failed_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code=legacy_response.error_code or _RETELL_TOOL_EXECUTION_FAILED_CODE,
        )

    def _load_duplicate_side_effect_response(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse | None:
        if parsed.tool_call_id is None:
            return None

        if not is_side_effecting_retell_tool(parsed.tool_name):
            return None

        idempotency_key = build_retell_tool_call_idempotency_key(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
        )
        stored_outcome = self.voice_calls.get_tool_call_outcome_by_idempotency_key(
            idempotency_key=idempotency_key,
        )

        if stored_outcome is None:
            return None

        response = deserialize_tool_call_outcome(stored_outcome)
        response.duplicate = True

        return response

    def _record_side_effect_outcome(
        self,
        parsed: ParsedRetellToolCall,
        response: RetellToolCallResponse,
    ) -> None:
        if parsed.tool_call_id is None:
            return

        voice_call = self.voice_calls.get_by_provider_call_id(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
        )

        if voice_call is None:
            voice_call, _ = self.voice_calls.get_or_create_voice_call(
                provider=self.provider,
                provider_call_id=parsed.provider_call_id,
            )

        occurred_at = parsed.occurred_at or datetime.now(UTC)
        idempotency_key = build_retell_tool_call_idempotency_key(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
        )

        self.voice_calls.record_tool_call_outcome(
            voice_call_id=voice_call.id,
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            event_type=build_retell_tool_call_event_type(parsed.tool_name),
            tool_call_id=parsed.tool_call_id,
            idempotency_key=idempotency_key,
            outcome=serialize_tool_call_outcome(response),
            occurred_at=occurred_at,
        )

    def _resolve_hold_owner_id(
        self,
        *,
        provider_call_id: str,
        owner_id: str | None,
        conversation_id: UUID | None = None,
    ) -> str | None:
        for candidate in [owner_id, provider_call_id]:
            if candidate is None:
                continue

            normalized = candidate.strip()

            if normalized:
                return normalized

        if conversation_id is not None:
            return str(conversation_id)

        return None

    def _resolve_hold_ttl_seconds(self, requested_ttl_seconds: int | None) -> int:
        if requested_ttl_seconds is not None:
            return requested_ttl_seconds

        return self.hold_service.ttl_seconds


def _as_check_availability_arguments(
    arguments: Any,
) -> CheckAvailabilityToolArguments:
    if not isinstance(arguments, CheckAvailabilityToolArguments):
        msg = "expected check availability tool arguments"
        raise TypeError(msg)

    return arguments


def _as_hold_arguments(
    arguments: Any,
) -> HoldAppointmentSlotToolArguments:
    if not isinstance(arguments, HoldAppointmentSlotToolArguments):
        msg = "expected hold appointment slot tool arguments"
        raise TypeError(msg)

    return arguments


def _as_release_arguments(
    arguments: Any,
) -> ReleaseAppointmentHoldToolArguments:
    if not isinstance(arguments, ReleaseAppointmentHoldToolArguments):
        msg = "expected release appointment hold tool arguments"
        raise TypeError(msg)

    return arguments


def _as_book_appointment_arguments(
    arguments: Any,
) -> BookAppointmentToolArguments:
    if not isinstance(arguments, BookAppointmentToolArguments):
        msg = "expected book appointment tool arguments"
        raise TypeError(msg)

    return arguments


def _as_cancel_appointment_arguments(
    arguments: Any,
) -> CancelAppointmentToolArguments:
    if not isinstance(arguments, CancelAppointmentToolArguments):
        msg = "expected cancel appointment tool arguments"
        raise TypeError(msg)

    return arguments


def _as_reschedule_appointment_arguments(
    arguments: Any,
) -> RescheduleAppointmentToolArguments:
    if not isinstance(arguments, RescheduleAppointmentToolArguments):
        msg = "expected reschedule appointment tool arguments"
        raise TypeError(msg)

    return arguments


def _as_resolve_patient_identity_arguments(
    arguments: Any,
) -> ResolvePatientIdentityToolArguments:
    if not isinstance(arguments, ResolvePatientIdentityToolArguments):
        msg = "expected resolve patient identity tool arguments"
        raise TypeError(msg)

    return arguments


def _as_confirm_patient_identity_arguments(
    arguments: Any,
) -> ConfirmPatientIdentityToolArguments:
    if not isinstance(arguments, ConfirmPatientIdentityToolArguments):
        msg = "expected confirm patient identity tool arguments"
        raise TypeError(msg)

    return arguments
