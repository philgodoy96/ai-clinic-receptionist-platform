from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from app.domain.appointments import (
    AppointmentCancellationRequest,
    AppointmentNotCancelableError,
    AppointmentNotFoundError,
    is_appointment_cancelable,
)
from app.domain.audit.enums import AuditActorType
from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConversationState,
    ExpectedResponseType,
    FieldIssue,
)
from app.domain.patient_identity_resolution import (
    PatientIdentityResolutionRequest,
    PatientResolutionMatchStatus,
)
from app.domain.scheduling.enums import AppointmentStatus
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.chat_booking_identity import (
    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY,
    APPOINTMENT_MANAGEMENT_EMPTY_OFFER_HELP,
    APPOINTMENT_MANAGEMENT_IDENTITY_KEY,
    ParsedPatientFields,
    ResolvedPatientContext,
    _dob_ambiguity_issue,
    _is_valid_iso_date,
    _merge_parsed_fields,
    appointment_management_missing_identity_prompt,
    build_resolved_patient_context_updates,
    merge_appointment_management_identity,
    read_resolved_patient_context,
)
from app.services.chat_confirmation import (
    ConfirmationDecision,
    ConfirmationType,
    normalize_patient_display_name,
    understand_confirmation,
)
from app.services.chat_offered_appointment_selection import (
    OfferedAppointmentSelectionResolution,
    OfferedAppointmentSelectionStatus,
    OfferedAppointmentView,
    build_offered_appointment_entry,
    format_ambiguous_appointment_selection_message,
    format_near_match_appointment_selection_message,
    format_no_useful_match_appointment_selection_message,
    prepare_appointment_selection_message,
    resolve_offered_appointment_selection_from_context,
)
from app.services.chat_selection_revision import (
    clear_pending_selection_context_updates,
    filter_offered_by_pending_ids,
    format_pending_subset_no_match_message,
    is_appointment_selection_revision_message,
    pending_selection_context_updates,
)
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.clinic_time import (
    ClinicTimeService,
    format_clinic_local_time_label,
    to_clinic_local_datetime,
)
from app.services.dob_ambiguity import (
    detect_ambiguous_numeric_dob,
    merge_dob_ambiguity_context_updates,
    try_resolve_pending_dob_ambiguity,
)
from app.services.patient_identity_resolution import PatientIdentityResolutionService

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

APPOINTMENT_MANAGEMENT_MODE_CANCEL = "cancel"
APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY = "patient_identity"
APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION = "appointment_selection"
APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION = "cancellation_confirmation"
APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED = "completed"

CHAT_CANCELLATION_SOURCE = "chat_cancellation"

_CANCELLATION_IDENTITY_REPROMPT_MESSAGE = (
    "I still need the patient's full name and date of birth to look up the appointment."
)
_CANCELLATION_IDENTITY_NAME_ONLY_MESSAGE = (
    "Thanks. What is the patient's full name?"
)
_CANCELLATION_IDENTITY_DOB_ONLY_MESSAGE = (
    "Thanks. What is the patient's date of birth?"
)
_CANCELLATION_PATIENT_NOT_FOUND_MESSAGE = (
    "I couldn't find a matching patient profile with that name and date of birth. "
    "Could you check the details and try again?"
)
_CANCELLATION_NO_UPCOMING_APPOINTMENTS_MESSAGE = (
    "I don't see any upcoming appointments that can be canceled. "
    "Is there anything else I can help you with?"
)
_CANCELLATION_APPOINTMENT_SELECTION_REPROMPT = (
    "Which appointment would you like to cancel?"
)
_CANCELLATION_APPOINTMENT_SELECTION_NO_MATCH = (
    "Please choose one of the appointments I listed."
)
_CANCELLATION_APPOINTMENT_SELECTION_AMBIGUOUS = (
    "I found more than one matching appointment. Which one would you like to cancel?"
)
_CANCELLATION_CONFIRMATION_REPROMPT = (
    "Please let me know if you'd like to cancel this appointment."
)
_CANCELLATION_CONFIRMATION_AMBIGUOUS_PREFIX = (
    "Please confirm whether you want me to cancel your"
)
_CANCELLATION_REJECTED_MESSAGE = (
    "Okay, I won't cancel that appointment. Is there anything else I can help with?"
)
_CANCELLATION_OWNERSHIP_MISMATCH_MESSAGE = (
    "I couldn't verify that appointment for the resolved patient, so I can't cancel it."
)
_CANCELLATION_APPOINTMENT_NOT_FOUND_MESSAGE = (
    "I couldn't find that appointment, so I can't cancel it."
)
_CANCELLATION_APPOINTMENT_NOT_CANCELABLE_MESSAGE = (
    "That appointment can no longer be cancelled."
)
_CANCELLATION_MISSING_SELECTION_MESSAGE = (
    "I don't have an appointment selected to cancel. "
    "Please tell me which appointment you'd like to cancel."
)
_CANCELLATION_SUCCESS_FOLLOW_UP_SUFFIX = " Is there anything else I can help with?"
_CANCEL_KEYWORDS = ("cancel", "cancellation")
class SchedulingMetadataForCancellation(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CancellationFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


class CancellationAppointmentSelectionStatus(StrEnum):
    UNIQUE = "unique"
    ZERO = "zero"
    AMBIGUOUS = "ambiguous"
    NEAR_MATCH = "near_match"


@dataclass(frozen=True, slots=True)
class CancellationAppointmentSelectionResult:
    status: CancellationAppointmentSelectionStatus
    selected_appointment: dict[str, str] | None = None
    matching_appointments: tuple[OfferedAppointmentView, ...] = ()
    signals_detected: bool = False
    constraints_description: str | None = None


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    appointment_id: UUID
    specialty_name: str
    doctor_name: str
    single_summary: str
    list_summary: str


class ChatAppointmentCancellationOrchestrator:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        appointment_cancellation: AppointmentCancellationService,
        scheduling_metadata: SchedulingMetadataForCancellation,
        clinic_time_service: ClinicTimeService,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
        self.appointment_cancellation = appointment_cancellation
        self.scheduling_metadata = scheduling_metadata
        self.clinic_time_service = clinic_time_service
        self.chat_turn_understanding_interpreter = chat_turn_understanding_interpreter

    def handle_patient_identity_intake(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
        parse_patient_fields: Callable[..., ParsedPatientFields],
    ) -> CancellationFlowResult:
        base_updates: dict[str, Any] = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
            ),
        }

        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            chat_context=chat_context,
            parse_patient_fields=parse_patient_fields,
        )

        # Preserve fields collected on earlier turns so the patient only has to
        # supply what is still missing, and so an ambiguous-DOB clarification can
        # resume with the name already on hand.
        identity = merge_appointment_management_identity(
            chat_context.get(APPOINTMENT_MANAGEMENT_IDENTITY_KEY),
            parsed,
            include_date_of_birth=dob_issue is None,
        )
        base_updates = {**base_updates, APPOINTMENT_MANAGEMENT_IDENTITY_KEY: identity}

        if dob_issue is not None:
            merge_dob_ambiguity_context_updates(
                base_updates,
                dob_issue=dob_issue,
                date_of_birth=None,
            )
            return CancellationFlowResult(
                intent="cancel_request",
                content=dob_issue.clarification_question or (
                    "Please clarify the patient's date of birth."
                ),
                chat_context_updates=base_updates,
            )

        full_name = identity.get("full_name")
        date_of_birth = identity.get("date_of_birth")
        merge_dob_ambiguity_context_updates(
            base_updates,
            dob_issue=None,
            date_of_birth=date_of_birth if isinstance(date_of_birth, str) else None,
        )
        if not isinstance(full_name, str) or not isinstance(date_of_birth, str):
            prompt = appointment_management_missing_identity_prompt(
                identity,
                both_prompt=_CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
                name_prompt=_CANCELLATION_IDENTITY_NAME_ONLY_MESSAGE,
                dob_prompt=_CANCELLATION_IDENTITY_DOB_ONLY_MESSAGE,
            )
            return CancellationFlowResult(
                intent="cancel_request",
                content=prompt or _CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
                chat_context_updates=base_updates,
            )

        full_name = normalize_patient_display_name(full_name)
        resolution = self.patient_identity_resolution.resolve(
            PatientIdentityResolutionRequest(
                patient_name=full_name,
                patient_date_of_birth=date.fromisoformat(date_of_birth),
                conversation_id=conversation.id,
                patient_email=identity.get("email"),
                patient_phone=identity.get("phone"),
                caller_claims_existing_patient=True,
                allow_demo_patient_creation=False,
            ),
        )

        if resolution.match_status is PatientResolutionMatchStatus.POSSIBLE_MATCH:
            question = (
                resolution.confirmation_question
                or resolution.suggested_response_text
                or "Is that the correct patient profile?"
            )
            return CancellationFlowResult(
                intent="cancel_request",
                content=question,
                chat_context_updates={
                    **base_updates,
                    "patient_resolution_id": resolution.patient_resolution_id,
                },
            )

        if resolution.match_status is PatientResolutionMatchStatus.MULTIPLE_MATCHES:
            return CancellationFlowResult(
                intent="cancel_request",
                content=(
                    resolution.suggested_response_text
                    or (
                        "I found more than one possible profile. "
                        "Could you provide the email or phone number on file?"
                    )
                ),
                chat_context_updates=base_updates,
            )

        if resolution.match_status is not PatientResolutionMatchStatus.EXACT_MATCH:
            # The lookup failed even though both fields were provided; drop the
            # buffered identity so the patient can correct it from scratch.
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates={**base_updates, APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None},
            )

        record = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=resolution.patient_resolution_id or "",
            conversation_id=conversation.id,
        )
        if record is None:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates=base_updates,
            )

        patient = self.patient_identity_resolution.patients.get_by_id(record.patient_id)
        resolved_name = patient.full_name if patient is not None else full_name
        resolved_updates = build_resolved_patient_context_updates(
            patient_id=str(record.patient_id),
            name=resolved_name,
            date_of_birth=(
                patient.date_of_birth.isoformat() if patient is not None else date_of_birth
            ),
            email=patient.email if patient is not None else identity.get("email"),
            patient_resolution_id=resolution.patient_resolution_id,
        )
        # Identity is now resolved; drop the partial intake buffer.
        resolved_updates[APPOINTMENT_MANAGEMENT_IDENTITY_KEY] = None

        start_from = self.clinic_time_service.clinic_now()
        cancelable = self.appointments.list_cancelable_for_patient(
            patient_id=record.patient_id,
            start_from=start_from,
        )

        return self._build_appointment_options_result(
            resolved_context_updates=resolved_updates,
            cancelable=cancelable,
        )

    def _build_appointment_options_result(
        self,
        *,
        resolved_context_updates: dict[str, Any],
        cancelable: Sequence[Appointment],
    ) -> CancellationFlowResult:
        if not cancelable:
            # The patient is resolved but has nothing to cancel. Move to a safe
            # completed state with an open follow-up so the next turn can close
            # politely or route a fresh intent, instead of trapping the user in
            # patient-identity intake.
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_NO_UPCOMING_APPOINTMENTS_MESSAGE,
                chat_context_updates={
                    **resolved_context_updates,
                    "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
                    "appointment_management_awaiting": (
                        APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
                    ),
                    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY: (
                        APPOINTMENT_MANAGEMENT_EMPTY_OFFER_HELP
                    ),
                    "cancellation_status": None,
                    "offered_appointments": None,
                    APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
                },
            )

        presentations = [
            self._present_appointment(appointment) for appointment in cancelable
        ]
        shared_context = {
            **resolved_context_updates,
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
        }

        if len(presentations) == 1:
            presentation = presentations[0]
            return CancellationFlowResult(
                intent="cancel_request",
                content=(
                    f"I found your {presentation.single_summary}. "
                    "Is this the appointment you want to cancel?"
                ),
                chat_context_updates={
                    **shared_context,
                    "appointment_management_awaiting": (
                        APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
                    ),
                    "selected_appointment_id": str(presentation.appointment_id),
                    "selected_appointment_summary": presentation.list_summary,
                },
            )

        options = "\n".join(
            f"{index}. {presentation.list_summary}."
            for index, presentation in enumerate(presentations, start=1)
        )
        count_label = _appointment_count_label(len(presentations))
        return CancellationFlowResult(
            intent="cancel_request",
            content=(
                f"I found {count_label} upcoming appointments:\n\n"
                f"{options}\n"
                "Which one would you like to cancel?"
            ),
            chat_context_updates={
                **shared_context,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
                ),
                "offered_appointments": [
                    build_offered_appointment_entry(
                        appointment_id=str(appointment.id),
                        summary=presentation.list_summary,
                        specialty_name=presentation.specialty_name,
                        doctor_name=presentation.doctor_name,
                        start_time=appointment.start_time,
                        doctor_id=str(appointment.doctor_id),
                        specialty_id=str(appointment.specialty_id),
                    )
                    for appointment, presentation in zip(
                        cancelable,
                        presentations,
                        strict=True,
                    )
                ],
            },
        )

    def list_appointments_for_resolved_patient(
        self,
        *,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult | None:
        """Reuse an already-resolved patient to list cancelable appointments.

        Returns ``None`` when no resolved patient identity is available, so the
        caller can fall back to the normal identity intake. Ownership is still
        validated by the repository/service before any cancellation executes.
        """
        resolved = read_resolved_patient_context(chat_context)
        if resolved is None:
            return None
        try:
            patient_id = UUID(resolved.patient_id)
        except ValueError:
            return None

        start_from = self.clinic_time_service.clinic_now()
        cancelable = self.appointments.list_cancelable_for_patient(
            patient_id=patient_id,
            start_from=start_from,
        )
        return self._build_appointment_options_result(
            resolved_context_updates=self._resolved_patient_context_updates(resolved),
            cancelable=cancelable,
        )

    @staticmethod
    def _resolved_patient_context_updates(
        resolved: ResolvedPatientContext,
    ) -> dict[str, Any]:
        return build_resolved_patient_context_updates(
            patient_id=resolved.patient_id,
            name=resolved.name,
            date_of_birth=resolved.date_of_birth,
            email=resolved.email,
            patient_resolution_id=resolved.patient_resolution_id,
        )

    def handle_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult:
        base_updates = self._appointment_selection_context_updates(chat_context)
        selection_message = prepare_appointment_selection_message(
            message,
            action_keywords=_CANCEL_KEYWORDS,
        )
        resolution = resolve_offered_appointment_selection_from_context(
            message=selection_message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            offered_filter=filter_offered_by_pending_ids,
        )
        return self._flow_result_for_appointment_selection(
            selection=resolution,
            chat_context=chat_context,
            base_updates=base_updates,
            original_message=message,
        )

    def revise_selected_appointment(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult:
        return self.handle_appointment_selection(
            message=message,
            chat_context=chat_context,
        )

    def _flow_result_for_appointment_selection(
        self,
        *,
        selection: OfferedAppointmentSelectionResolution,
        chat_context: dict[str, Any],
        base_updates: dict[str, Any],
        original_message: str,
    ) -> CancellationFlowResult:
        result = selection.result
        offered_candidates = selection.offered_candidates
        pending_active = selection.pending_refinement_active

        if result.status is OfferedAppointmentSelectionStatus.UNIQUE:
            selected = result.selected_appointment
            assert selected is not None
            summary = selected["summary"]
            confirmation_summary = _confirmation_summary_from_list_summary(summary)
            return CancellationFlowResult(
                intent="cancel_request",
                content=(
                    f"Please confirm: should I cancel your {confirmation_summary}?"
                ),
                chat_context_updates={
                    **self._resolved_patient_context(chat_context),
                    **clear_pending_selection_context_updates(),
                    "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
                    "appointment_management_awaiting": (
                        APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
                    ),
                    "selected_appointment_id": selected["appointment_id"],
                    "selected_appointment_summary": summary,
                    **(
                        {"offered_appointments": chat_context["offered_appointments"]}
                        if chat_context.get("offered_appointments") is not None
                        else {}
                    ),
                },
            )

        if result.status is OfferedAppointmentSelectionStatus.AMBIGUOUS:
            ambiguous_content = (
                format_ambiguous_appointment_selection_message(
                    matching=result.matching_appointments,
                    action_verb="cancel",
                )
                if result.matching_appointments
                else _CANCELLATION_APPOINTMENT_SELECTION_AMBIGUOUS
            )
            return CancellationFlowResult(
                intent="cancel_request",
                content=ambiguous_content,
                chat_context_updates={
                    **base_updates,
                    **pending_selection_context_updates(result.matching_appointments),
                },
            )

        if result.status is OfferedAppointmentSelectionStatus.NEAR_MATCH:
            near_content = format_near_match_appointment_selection_message(
                constraints_description=(
                    result.constraints_description or "that description"
                ),
                matching=result.matching_appointments,
            )
            return CancellationFlowResult(
                intent="cancel_request",
                content=near_content,
                chat_context_updates=base_updates,
            )

        if (
            not result.signals_detected
            and _message_contains_cancel_keyword(original_message)
        ):
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_SELECTION_REPROMPT,
                chat_context_updates=base_updates,
            )

        if pending_active and result.signals_detected:
            return CancellationFlowResult(
                intent="cancel_request",
                content=format_pending_subset_no_match_message(
                    scope_description=result.constraints_description,
                    candidates=offered_candidates,
                ),
                chat_context_updates={
                    **base_updates,
                    **pending_selection_context_updates(offered_candidates),
                },
            )

        return CancellationFlowResult(
            intent="cancel_request",
            content=format_no_useful_match_appointment_selection_message(
                constraints_description=result.constraints_description,
                offered=selection.offered_all,
            ),
            chat_context_updates=base_updates,
        )

    def resolve_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> CancellationAppointmentSelectionResult:
        resolution = resolve_offered_appointment_selection_from_context(
            message=message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            offered_filter=filter_offered_by_pending_ids,
        )
        result = resolution.result
        return CancellationAppointmentSelectionResult(
            status=CancellationAppointmentSelectionStatus(result.status.value),
            selected_appointment=result.selected_appointment,
            matching_appointments=result.matching_appointments,
            signals_detected=result.signals_detected,
            constraints_description=result.constraints_description,
        )

    def reprompt_for_appointment_selection(self) -> CancellationFlowResult:
        return CancellationFlowResult(
            intent="cancel_request",
            content=_CANCELLATION_APPOINTMENT_SELECTION_REPROMPT,
            chat_context_updates={},
        )

    def reprompt_for_cancellation_confirmation(self) -> CancellationFlowResult:
        return CancellationFlowResult(
            intent="cancel_request",
            content=_CANCELLATION_CONFIRMATION_REPROMPT,
            chat_context_updates={},
        )

    def handle_cancellation_confirmation(
        self,
        *,
        message: str,
        conversation_id: UUID,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult:
        if is_appointment_selection_revision_message(
            message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            action_keywords=_CANCEL_KEYWORDS,
        ):
            return self.revise_selected_appointment(
                message=message,
                chat_context=chat_context,
            )

        understanding = understand_confirmation(
            confirmation_type=ConfirmationType.CANCELLATION_CONFIRMATION,
            message=message,
        )

        if understanding.decision is ConfirmationDecision.REJECTED:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_REJECTED_MESSAGE,
                chat_context_updates=self._cancellation_declined_context_updates(
                    chat_context,
                ),
            )

        if understanding.decision in {
            ConfirmationDecision.UNCLEAR,
            ConfirmationDecision.WANTS_CHANGE,
        }:
            return self._reprompt_cancellation_confirmation(chat_context)

        selected_appointment_id = chat_context.get("selected_appointment_id")
        resolved_patient_id = chat_context.get("resolved_patient_id")
        selected_summary = chat_context.get("selected_appointment_summary")

        if (
            not isinstance(selected_appointment_id, str)
            or not isinstance(resolved_patient_id, str)
            or not isinstance(selected_summary, str)
        ):
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_MISSING_SELECTION_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        try:
            appointment_id = UUID(selected_appointment_id)
            patient_id = UUID(resolved_patient_id)
        except ValueError:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_NOT_FOUND_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        appointment = self.appointments.get_by_id(appointment_id)
        if appointment is None:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_NOT_FOUND_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        if appointment.patient_id != patient_id:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_OWNERSHIP_MISMATCH_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        if (
            not is_appointment_cancelable(appointment.status)
            and appointment.status != AppointmentStatus.CANCELLED
        ):
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_NOT_CANCELABLE_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        confirmation_summary = _confirmation_summary_from_list_summary(selected_summary)
        idempotency_key = (
            f"chat-cancel:{conversation_id}:{selected_appointment_id}"
        )
        request = AppointmentCancellationRequest(
            appointment_id=appointment_id,
            explicit_confirmation=True,
            idempotency_key=idempotency_key,
            source=CHAT_CANCELLATION_SOURCE,
            actor_type=AuditActorType.CHAT,
            actor_id=str(conversation_id),
            conversation_id=str(conversation_id),
        )

        try:
            result = self.appointment_cancellation.cancel_appointment(request)
        except AppointmentNotFoundError:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_NOT_FOUND_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )
        except AppointmentNotCancelableError:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_NOT_CANCELABLE_MESSAGE,
                chat_context_updates=self._cancellation_completed_context_updates(
                    chat_context,
                ),
            )

        if result.already_cancelled:
            content = (
                f"Your {confirmation_summary} has already been cancelled."
                f"{_CANCELLATION_SUCCESS_FOLLOW_UP_SUFFIX}"
            )
        else:
            content = (
                f"Your {confirmation_summary} has been cancelled."
                f"{_CANCELLATION_SUCCESS_FOLLOW_UP_SUFFIX}"
            )

        return CancellationFlowResult(
            intent="cancel_request",
            content=content,
            chat_context_updates=self._cancellation_succeeded_context_updates(
                chat_context,
                cancelled_appointment_summary=selected_summary,
            ),
        )

    def _reprompt_cancellation_confirmation(
        self,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult:
        selected_summary = chat_context.get("selected_appointment_summary")
        if isinstance(selected_summary, str):
            confirmation_summary = _confirmation_summary_from_list_summary(
                selected_summary,
            )
            content = (
                f"{_CANCELLATION_CONFIRMATION_AMBIGUOUS_PREFIX} "
                f"{confirmation_summary}."
            )
        else:
            content = _CANCELLATION_CONFIRMATION_REPROMPT

        return CancellationFlowResult(
            intent="cancel_request",
            content=content,
            chat_context_updates=self._cancellation_confirmation_context_updates(
                chat_context,
            ),
        )

    def _cancellation_confirmation_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        updates = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
            ),
            **self._resolved_patient_context(chat_context),
        }
        if isinstance(chat_context.get("selected_appointment_id"), str):
            updates["selected_appointment_id"] = chat_context["selected_appointment_id"]
        if isinstance(chat_context.get("selected_appointment_summary"), str):
            updates["selected_appointment_summary"] = chat_context[
                "selected_appointment_summary"
            ]
        if chat_context.get("offered_appointments") is not None:
            updates["offered_appointments"] = chat_context["offered_appointments"]
        return updates

    def _cancellation_succeeded_context_updates(
        self,
        chat_context: dict[str, Any],
        *,
        cancelled_appointment_summary: str,
    ) -> dict[str, Any]:
        return {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
            "cancellation_status": "cancelled",
            "cancelled_appointment_summary": cancelled_appointment_summary,
            **self._resolved_patient_context(chat_context),
            "selected_appointment_id": None,
            "selected_appointment_summary": None,
            "offered_appointments": None,
        }

    def _cancellation_declined_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._cancellation_completed_context_updates(chat_context),
            "cancellation_status": "declined",
        }

    def _cancellation_completed_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
            **self._resolved_patient_context(chat_context),
            "selected_appointment_id": None,
            "selected_appointment_summary": None,
            "offered_appointments": None,
        }

    def _resolve_patient_fields(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
        parse_patient_fields: Callable[..., ParsedPatientFields],
    ) -> tuple[ParsedPatientFields, FieldIssue | None]:
        confirmed_iso, rejection_issue = try_resolve_pending_dob_ambiguity(
            message,
            chat_context,
        )
        if rejection_issue is not None:
            return ParsedPatientFields(), rejection_issue

        deterministic = parse_patient_fields(message, booking_context=True)
        understanding = self._interpret_identity_turn(
            message=message,
            chat_context=chat_context,
        )
        if understanding is None or self._should_use_deterministic_only(understanding):
            if confirmed_iso is not None:
                return (
                    ParsedPatientFields(
                        full_name=deterministic.full_name,
                        date_of_birth=confirmed_iso,
                        email=deterministic.email,
                        phone=deterministic.phone,
                    ),
                    None,
                )
            return deterministic, detect_ambiguous_numeric_dob(message)

        ctu_fields, dob_issue = self._validated_fields_from_understanding(understanding)
        if dob_issue is None:
            dob_issue = detect_ambiguous_numeric_dob(message)
        merged = _merge_parsed_fields(deterministic, ctu_fields)
        if deterministic.phone and not merged.phone:
            merged = ParsedPatientFields(
                full_name=merged.full_name,
                date_of_birth=merged.date_of_birth,
                email=merged.email,
                phone=deterministic.phone,
            )
        if confirmed_iso is not None:
            merged = ParsedPatientFields(
                full_name=merged.full_name,
                date_of_birth=confirmed_iso,
                email=merged.email,
                phone=merged.phone,
            )
            dob_issue = None
        return merged, dob_issue

    def _interpret_identity_turn(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> ChatTurnUnderstandingResult | None:
        if self.chat_turn_understanding_interpreter is None:
            return None

        request = ChatTurnUnderstandingRequest(
            conversation_state=ConversationState.CANCELLATION_INTAKE,
            expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
            latest_user_message=message,
            allowed_intents=[ChatTurnIntent.PATIENT_IDENTITY_PROVIDED],
            current_context={
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
                ),
                **{
                    key: chat_context[key]
                    for key in ("resolved_patient_id", "resolved_patient_name")
                    if key in chat_context
                },
            },
        )
        try:
            return self.chat_turn_understanding_interpreter.interpret(request)
        except Exception:
            logger.exception(
                "chat turn understanding interpreter failed during cancellation identity intake",
            )
            return None

    def _should_use_deterministic_only(
        self,
        understanding: ChatTurnUnderstandingResult,
    ) -> bool:
        if understanding.intent is ChatTurnIntent.FALLBACK:
            return True
        return understanding.confidence < _CTU_LOW_CONFIDENCE_THRESHOLD

    def _validated_fields_from_understanding(
        self,
        understanding: ChatTurnUnderstandingResult,
    ) -> tuple[ParsedPatientFields, FieldIssue | None]:
        extracted = understanding.extracted_fields
        dob_issue = _dob_ambiguity_issue(understanding)

        full_name: str | None = None
        if extracted.patient_name:
            normalized_name = normalize_patient_display_name(extracted.patient_name)
            if normalized_name:
                full_name = normalized_name

        date_of_birth: str | None = None
        if dob_issue is None and extracted.date_of_birth and _is_valid_iso_date(
            extracted.date_of_birth,
        ):
            date_of_birth = extracted.date_of_birth

        email: str | None = None
        if extracted.email:
            email = extracted.email

        return (
            ParsedPatientFields(
                full_name=full_name,
                date_of_birth=date_of_birth,
                email=email,
                phone=None,
            ),
            dob_issue,
        )

    def _present_appointment(self, appointment: Appointment) -> _AppointmentPresentation:
        doctor_name = self._resolve_doctor_name(appointment.doctor_id)
        specialty_name = self._resolve_specialty_name(appointment.specialty_id)
        clinic_tz = self.clinic_time_service.timezone
        localized_start = to_clinic_local_datetime(appointment.start_time, clinic_tz)
        weekday = localized_start.strftime("%A")
        time_label = format_clinic_local_time_label(appointment.start_time, clinic_tz)

        list_summary = (
            f"{specialty_name} with {doctor_name} on {weekday} at {time_label}"
        )
        single_summary = (
            f"{specialty_name} appointment with {doctor_name} "
            f"on {weekday} at {time_label}"
        )

        return _AppointmentPresentation(
            appointment_id=appointment.id,
            specialty_name=specialty_name,
            doctor_name=doctor_name,
            single_summary=single_summary,
            list_summary=list_summary,
        )

    def _appointment_selection_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        updates = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
            ),
            **self._resolved_patient_context(chat_context),
        }
        if chat_context.get("offered_appointments") is not None:
            updates["offered_appointments"] = chat_context["offered_appointments"]
        return updates

    def _resolved_patient_context(self, chat_context: dict[str, Any]) -> dict[str, Any]:
        return {
            key: chat_context[key]
            for key in (
                "resolved_patient_id",
                "resolved_patient_name",
                "resolved_patient_date_of_birth",
                "resolved_patient_email",
                "patient_resolution_id",
            )
            if key in chat_context
        }

    def _resolve_doctor_name(self, doctor_id: UUID) -> str:
        for doctor in self.scheduling_metadata.list_doctors():
            if doctor.id == doctor_id:
                return doctor.full_name

        return "your doctor"

    def _resolve_specialty_name(self, specialty_id: UUID) -> str:
        for specialty in self.scheduling_metadata.list_specialties():
            if specialty.id == specialty_id:
                return specialty.name

        return "your appointment"


def _appointment_count_label(count: int) -> str:
    if count == 1:
        return "one"
    if count == 2:
        return "two"
    if count == 3:
        return "three"
    return str(count)


def _confirmation_summary_from_list_summary(list_summary: str) -> str:
    specialty, separator, remainder = list_summary.partition(" with ")
    if separator:
        return f"{specialty} appointment with {remainder}"
    return list_summary


def _message_contains_cancel_keyword(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in _CANCEL_KEYWORDS)


# Backward-compatible re-exports for reschedule slot/appointment selection helpers.
