from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingHoldExpiredError,
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
    AppointmentReschedulingSlotUnavailableError,
    is_appointment_reschedulable,
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
from app.domain.scheduling.expressions import DateExpressionKind
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.schemas.retell_tools import CheckAvailabilityToolArguments
from app.schemas.scheduling_expressions import DateExpressionSchema
from app.services.appointment_holds import (
    AppointmentHoldService,
    AppointmentHoldStoreUnavailableError,
    AppointmentSlotAlreadyHeldError,
)
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
)
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    _appointment_count_label,
    _confirmation_summary_from_list_summary,
)
from app.services.chat_appointment_intake import EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS
from app.services.chat_booking_identity import (
    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY,
    APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING,
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
    doctor_name_in_message,
    format_ambiguous_appointment_selection_message,
    format_near_match_appointment_selection_message,
    format_no_useful_match_appointment_selection_message,
    prepare_appointment_selection_message,
    resolve_offered_appointment_selection_from_context,
)
from app.services.chat_offered_appointment_selection import (
    extract_option_number_index as _extract_option_number_index,
)
from app.services.chat_offered_appointment_selection import (
    extract_ordinal_index as _extract_ordinal_index,
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
    format_clinic_local_slot_summary,
    format_clinic_local_time_label,
    to_clinic_local_datetime,
)
from app.services.date_parsing import DateParseStatus, NaturalLanguageDateParser
from app.services.dob_ambiguity import (
    detect_ambiguous_numeric_dob,
    merge_dob_ambiguity_context_updates,
    try_resolve_pending_dob_ambiguity,
)
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    DoctorAttributedAvailabilitySlot,
    SchedulingService,
)
from app.services.scheduling_availability import SchedulingAvailabilityResolver
from app.services.time_preferences import (
    TimePreferenceParser,
    TimePreferenceStatus,
    TimeWindow,
    is_time_in_window,
)

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE = "reschedule"
APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE = "new_time_preference"
APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION = "new_slot_selection"
APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION = "reschedule_confirmation"
APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED = "completed"

CHAT_RESCHEDULE_SOURCE = "chat_reschedule"

_MAX_RESCHEDULE_OFFERED_SLOTS = 5

_RESCHEDULE_AVAILABILITY_RANGE_THIS_WEEK = "this_week"
_RESCHEDULE_AVAILABILITY_RANGE_NEXT_WEEK = "next_week"

_SOONEST_MARKERS = (
    "soonest",
    "earliest",
    "as soon as possible",
    "asap",
    "first available",
)

_AVAILABILITY_RANGE_PATTERN = re.compile(r"\b(this|next)\s+week\b", re.IGNORECASE)
_BARE_WEEKDAY_PATTERN = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_PREFIXED_WEEKDAY_PATTERN = re.compile(
    r"\b(?:this|next)\s+"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RESCHEDULE_KEYWORDS = (
    "reschedule",
    "move appointment",
    "move my appointment",
    "move an appointment",
)
_RESCHEDULE_SINGLE_CONFIRMED_PHRASES = (
    "that's the one",
    "that is the one",
)
_RESCHEDULE_SINGLE_REJECTED_PHRASES = (
    "not that one",
    "that's not it",
    "that is not it",
)
_ORDINAL_WORDS = frozenset({"first", "second", "third"})
_SPECIALTY_SELECTION_PATTERN = re.compile(
    r"\bthe\s+([a-z][a-z\s-]*?)\s+one\b",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = re.compile(r"^[.,!?;:]+|[.,!?;:]+$")

RESCHEDULE_IDENTITY_ENTRY_MESSAGE = (
    "Of course. I can look it up first. What is the patient's full name and date of birth?"
)
RESCHEDULE_IDENTITY_REPROMPT_MESSAGE = (
    "I still need the patient's full name and date of birth to look up the appointment."
)
RESCHEDULE_IDENTITY_NAME_ONLY_MESSAGE = (
    "Thanks. What is the patient's full name?"
)
RESCHEDULE_IDENTITY_DOB_ONLY_MESSAGE = (
    "Thanks. What is the patient's date of birth?"
)
RESCHEDULE_PATIENT_NOT_FOUND_MESSAGE = (
    "I couldn't find a matching patient profile with that name and date of birth. "
    "Could you check the details and try again?"
)
RESCHEDULE_NO_UPCOMING_APPOINTMENTS_MESSAGE = (
    "I don't see any upcoming appointments that can be rescheduled. "
    "Would you like to schedule a new appointment instead?"
)
RESCHEDULE_APPOINTMENT_SELECTION_REPROMPT = (
    "Which appointment would you like to reschedule?"
)
RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH = (
    "Please choose one of the appointments I listed."
)
RESCHEDULE_APPOINTMENT_SELECTION_AMBIGUOUS = (
    "I found more than one matching appointment. Which one would you like to reschedule?"
)
RESCHEDULE_APPOINTMENT_REJECTED_MESSAGE = (
    "Okay. I won't reschedule that appointment. Is there anything else I can help with?"
)
RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT = (
    "What day or time would you prefer instead?"
)
RESCHEDULE_NO_AVAILABILITY_MESSAGE = (
    "I don't see openings for that time. Would you like to try another day or time?"
)
RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT = (
    "Please choose one of the offered times."
)
RESCHEDULE_NEW_SLOT_SELECTION_AMBIGUOUS = (
    "I found more than one matching time. Which one would you like?"
)
RESCHEDULE_NEW_SLOT_SELECTION_PREFERENCE_HINT = (
    "Please choose one of the offered times, or tell me another day or time you prefer."
)
RESCHEDULE_HOLD_UNAVAILABLE_MESSAGE = (
    "That time is no longer available. Would you like to choose another time?"
)
RESCHEDULE_HOLD_EXPIRED_MESSAGE = (
    "That time is no longer being held. Would you like to choose another time?"
)
RESCHEDULE_SLOT_UNAVAILABLE_MESSAGE = (
    "That time is no longer available. Would you like to choose another time?"
)
RESCHEDULE_APPOINTMENT_NOT_RESCHEDULABLE_MESSAGE = (
    "I'm sorry, that appointment can no longer be rescheduled."
)
RESCHEDULE_OWNERSHIP_MISMATCH_MESSAGE = (
    "I'm sorry, I can't reschedule that appointment with the information provided."
)
RESCHEDULE_UNEXPECTED_FAILURE_MESSAGE = (
    "I couldn't complete the reschedule right now. Please try again or contact the clinic."
)
RESCHEDULE_SUCCESS_FOLLOW_UP_SUFFIX = " Is there anything else I can help with?"
RESCHEDULE_CONFIRMATION_REPROMPT_STUB = (
    "Please confirm whether you want me to reschedule your appointment."
)

_ISO_DATETIME_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)\b",
)


@dataclass(frozen=True, slots=True)
class RescheduleFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _RescheduleDateExtraction:
    normalized_date: str | None = None
    requires_clarification: bool = False


@dataclass(frozen=True, slots=True)
class _RescheduleTimeExtraction:
    window: dict[str, str] | None = None
    requires_clarification: bool = False


@dataclass(frozen=True, slots=True)
class _ReschedulePreferenceExtraction:
    requested_date: str | None = None
    search_start_date: str | None = None
    search_end_date: str | None = None
    time_window: dict[str, str] | None = None
    exact_time: str | None = None
    requires_clarification: bool = False


class RescheduleAppointmentSelectionStatus(StrEnum):
    UNIQUE = "unique"
    ZERO = "zero"
    AMBIGUOUS = "ambiguous"
    NEAR_MATCH = "near_match"


class RescheduleSlotSelectionStatus(StrEnum):
    UNIQUE = "unique"
    ZERO = "zero"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class _OfferedRescheduleSlotView:
    availability_slot_id: str
    summary: str
    weekday: str
    time_label: str
    doctor_id: str | None = None
    doctor_name: str = ""
    specialty_id: str | None = None
    specialty_name: str = ""
    start_time: str = ""


@dataclass(frozen=True, slots=True)
class RescheduleAppointmentSelectionResult:
    status: RescheduleAppointmentSelectionStatus
    selected_appointment: dict[str, str] | None = None
    matching_appointments: tuple[OfferedAppointmentView, ...] = ()
    signals_detected: bool = False
    constraints_description: str | None = None


@dataclass(frozen=True, slots=True)
class RescheduleSlotSelectionResult:
    status: RescheduleSlotSelectionStatus
    selected_slot: _OfferedRescheduleSlotView | None = None


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    appointment_id: UUID
    specialty_name: str
    doctor_name: str
    single_summary: str
    list_summary: str


class SchedulingMetadataForRescheduling(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


class ChatAppointmentReschedulingOrchestrator:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        scheduling: SchedulingService,
        scheduling_metadata: SchedulingMetadataForRescheduling,
        clinic_time_service: ClinicTimeService,
        date_parser: NaturalLanguageDateParser | None = None,
        time_preference_parser: TimePreferenceParser | None = None,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
        appointment_holds: AppointmentHoldService | None = None,
        appointment_rescheduling: AppointmentReschedulingService | None = None,
        chat_appointment_hold_ttl_seconds: int = 600,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
        self.scheduling = scheduling
        self.scheduling_metadata = scheduling_metadata
        self.clinic_time_service = clinic_time_service
        self.date_parser = date_parser or NaturalLanguageDateParser()
        self.time_preference_parser = time_preference_parser or TimePreferenceParser()
        self.chat_turn_understanding_interpreter = chat_turn_understanding_interpreter
        if appointment_holds is None:
            msg = "appointment_holds is required for reschedule slot holds"
            raise ValueError(msg)
        self.appointment_holds = appointment_holds
        self._chat_appointment_hold_ttl_seconds = chat_appointment_hold_ttl_seconds
        if appointment_rescheduling is None:
            msg = "appointment_rescheduling is required for reschedule confirmation"
            raise ValueError(msg)
        self.appointment_rescheduling = appointment_rescheduling

    def handle_patient_identity_intake(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
        parse_patient_fields: Callable[..., ParsedPatientFields],
    ) -> RescheduleFlowResult:
        base_updates: dict[str, Any] = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
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
            return RescheduleFlowResult(
                intent="reschedule_request",
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
                both_prompt=RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
                name_prompt=RESCHEDULE_IDENTITY_NAME_ONLY_MESSAGE,
                dob_prompt=RESCHEDULE_IDENTITY_DOB_ONLY_MESSAGE,
            )
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=prompt or RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
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
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=question,
                chat_context_updates={
                    **base_updates,
                    "patient_resolution_id": resolution.patient_resolution_id,
                },
            )

        if resolution.match_status is PatientResolutionMatchStatus.MULTIPLE_MATCHES:
            return RescheduleFlowResult(
                intent="reschedule_request",
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
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates={**base_updates, APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None},
            )

        record = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=resolution.patient_resolution_id or "",
            conversation_id=conversation.id,
        )
        if record is None:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_PATIENT_NOT_FOUND_MESSAGE,
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
        reschedulable = self.appointments.list_reschedulable_for_patient(
            patient_id=record.patient_id,
            start_from=start_from,
        )
        return self._build_appointment_options_result(
            resolved_context_updates=resolved_updates,
            reschedulable=reschedulable,
        )

    def _build_appointment_options_result(
        self,
        *,
        resolved_context_updates: dict[str, Any],
        reschedulable: Sequence[Appointment],
    ) -> RescheduleFlowResult:
        if not reschedulable:
            # The patient is resolved but has nothing to reschedule. Move to a
            # safe completed state and offer to book instead, so the next "yes"
            # routes to scheduling rather than re-asking for patient identity.
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_NO_UPCOMING_APPOINTMENTS_MESSAGE,
                chat_context_updates={
                    **resolved_context_updates,
                    "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
                    "appointment_management_awaiting": (
                        APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
                    ),
                    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY: (
                        APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING
                    ),
                    "reschedule_status": None,
                    "offered_appointments": None,
                    APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
                },
            )

        presentations = [
            self._present_appointment(appointment) for appointment in reschedulable
        ]
        shared_context = {
            **resolved_context_updates,
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
            ),
            "offered_appointments": [
                build_offered_appointment_entry(
                    appointment_id=str(presentation.appointment_id),
                    summary=presentation.list_summary,
                    specialty_name=presentation.specialty_name,
                    doctor_name=presentation.doctor_name,
                    start_time=appointment.start_time,
                    doctor_id=str(appointment.doctor_id),
                    specialty_id=str(appointment.specialty_id),
                )
                for appointment, presentation in zip(
                    reschedulable,
                    presentations,
                    strict=True,
                )
            ],
        }

        if len(presentations) == 1:
            presentation = presentations[0]
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=(
                    f"I found your {presentation.single_summary}. "
                    "Is this the appointment you want to reschedule?"
                ),
                chat_context_updates=shared_context,
            )

        options = "\n".join(
            f"{index}. {presentation.list_summary}."
            for index, presentation in enumerate(presentations, start=1)
        )
        count_label = _appointment_count_label(len(presentations))
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=(
                f"I found {count_label} upcoming appointments:\n\n"
                f"{options}\n"
                "Which one would you like to reschedule?"
            ),
            chat_context_updates=shared_context,
        )

    def list_appointments_for_resolved_patient(
        self,
        *,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult | None:
        """Reuse an already-resolved patient to list reschedulable appointments.

        Returns ``None`` when no resolved patient identity is available so the
        caller can fall back to the normal identity intake. Ownership is still
        validated by the repository/service before any reschedule executes.
        """
        resolved = read_resolved_patient_context(chat_context)
        if resolved is None:
            return None
        try:
            patient_id = UUID(resolved.patient_id)
        except ValueError:
            return None

        start_from = self.clinic_time_service.clinic_now()
        reschedulable = self.appointments.list_reschedulable_for_patient(
            patient_id=patient_id,
            start_from=start_from,
        )
        return self._build_appointment_options_result(
            resolved_context_updates=self._resolved_patient_context_updates(resolved),
            reschedulable=reschedulable,
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
    ) -> RescheduleFlowResult:
        base_updates = self._appointment_selection_context_updates(chat_context)
        selection_message = prepare_appointment_selection_message(
            message,
            action_keywords=_RESCHEDULE_KEYWORDS,
        )

        resolution = resolve_offered_appointment_selection_from_context(
            message=selection_message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            offered_filter=filter_offered_by_pending_ids,
        )
        offered_all = resolution.offered_all
        pending_active = resolution.pending_refinement_active

        if len(offered_all) == 1 and not pending_active:
            single_result = self._resolve_single_offered_appointment_response(
                message=selection_message,
                offered=offered_all,
                chat_context=chat_context,
            )
            if single_result is not None:
                return single_result

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
    ) -> RescheduleFlowResult:
        result = self.handle_appointment_selection(
            message=message,
            chat_context=chat_context,
        )
        if result.chat_context_updates.get("selected_appointment_id"):
            result = RescheduleFlowResult(
                intent=result.intent,
                content=result.content,
                chat_context_updates={
                    **result.chat_context_updates,
                    **self._clear_stale_reschedule_slot_hold_state(),
                },
            )
        return result

    def _flow_result_for_appointment_selection(
        self,
        *,
        selection: OfferedAppointmentSelectionResolution,
        chat_context: dict[str, Any],
        base_updates: dict[str, Any],
        original_message: str,
    ) -> RescheduleFlowResult:
        result = selection.result
        offered_all = selection.offered_all
        offered_candidates = selection.offered_candidates
        pending_active = selection.pending_refinement_active

        if result.status is OfferedAppointmentSelectionStatus.UNIQUE:
            selected = result.selected_appointment
            assert selected is not None
            selected_view = self._offered_view_from_selection(selected, offered_all)
            if selected_view is None:
                return RescheduleFlowResult(
                    intent="reschedule_request",
                    content=RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH,
                    chat_context_updates=base_updates,
                )
            return self._build_appointment_selected_result(
                selected=selected_view,
                chat_context=chat_context,
                clear_pending_refinement=True,
            )

        if result.status is OfferedAppointmentSelectionStatus.AMBIGUOUS:
            ambiguous_content = (
                format_ambiguous_appointment_selection_message(
                    matching=result.matching_appointments,
                    action_verb="reschedule",
                )
                if result.matching_appointments
                else RESCHEDULE_APPOINTMENT_SELECTION_AMBIGUOUS
            )
            return RescheduleFlowResult(
                intent="reschedule_request",
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
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=near_content,
                chat_context_updates=base_updates,
            )

        if (
            not result.signals_detected
            and _message_contains_reschedule_keyword(original_message)
        ):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_SELECTION_REPROMPT,
                chat_context_updates=base_updates,
            )

        if pending_active and result.signals_detected:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=format_pending_subset_no_match_message(
                    scope_description=result.constraints_description,
                    candidates=offered_candidates,
                ),
                chat_context_updates={
                    **base_updates,
                    **pending_selection_context_updates(offered_candidates),
                },
            )

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=format_no_useful_match_appointment_selection_message(
                constraints_description=result.constraints_description,
                offered=offered_all,
            ),
            chat_context_updates=base_updates,
        )

    def resolve_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleAppointmentSelectionResult:
        resolution = resolve_offered_appointment_selection_from_context(
            message=message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            offered_filter=filter_offered_by_pending_ids,
        )
        result = resolution.result
        return RescheduleAppointmentSelectionResult(
            status=RescheduleAppointmentSelectionStatus(result.status.value),
            selected_appointment=result.selected_appointment,
            matching_appointments=result.matching_appointments,
            signals_detected=result.signals_detected,
            constraints_description=result.constraints_description,
        )

    def handle_new_time_preference(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        if is_appointment_selection_revision_message(
            message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            action_keywords=_RESCHEDULE_KEYWORDS,
        ):
            return self.revise_selected_appointment(
                message=message,
                chat_context=chat_context,
            )

        base_updates = {
            **self._reschedule_flow_context(chat_context),
            **self._clear_stale_reschedule_slot_hold_state(),
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
            ),
        }

        preference = self._extract_reschedule_preference(message)
        if preference.requires_clarification:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT,
                chat_context_updates=base_updates,
            )

        doctor_id_raw = chat_context.get("selected_appointment_doctor_id")
        specialty_id_raw = chat_context.get("selected_appointment_specialty_id")
        doctor_name = _optional_str(chat_context.get("selected_appointment_doctor_name"))
        specialty_name = _optional_str(chat_context.get("selected_appointment_specialty_name"))

        use_doctor_target = isinstance(doctor_id_raw, str) and bool(doctor_id_raw)
        provider_label = doctor_name or specialty_name or "your provider"

        filtered_doctor_slots: list[AvailabilitySlot] = []
        filtered_attributed_slots: list[DoctorAttributedAvailabilitySlot] = []
        doctor_slots: Sequence[AvailabilitySlot] = []
        attributed_slots: Sequence[DoctorAttributedAvailabilitySlot] = []

        if use_doctor_target:
            assert isinstance(doctor_id_raw, str)
            doctor_slots = self._search_doctor_availability(
                doctor_id=UUID(doctor_id_raw),
                preference=preference,
            )
            filtered_doctor_slots = self._filter_doctor_slots_by_preference(
                doctor_slots,
                preference=preference,
            )
        elif isinstance(specialty_id_raw, str) and specialty_id_raw:
            attributed_slots = self._search_specialty_availability(
                specialty_id=UUID(specialty_id_raw),
                preference=preference,
            )
            filtered_attributed_slots = self._filter_attributed_slots_by_preference(
                attributed_slots,
                preference=preference,
            )
            if attributed_slots and not doctor_name:
                provider_label = specialty_name or "your specialty"
        else:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT,
                chat_context_updates=base_updates,
            )

        has_availability = bool(filtered_doctor_slots or filtered_attributed_slots)
        if not has_availability:
            no_slot_updates = {
                **base_updates,
                "reschedule_offered_slots": None,
            }
            if preference.requested_date is not None:
                no_slot_updates["reschedule_requested_date"] = preference.requested_date
            if preference.time_window is not None:
                no_slot_updates["reschedule_requested_time_window"] = preference.time_window

            if preference.exact_time is not None:
                day_preference = _ReschedulePreferenceExtraction(
                    requested_date=preference.requested_date,
                    search_start_date=preference.search_start_date,
                    search_end_date=preference.search_end_date,
                    time_window=preference.time_window,
                )
                day_slots: Sequence[AvailabilitySlot] | Sequence[DoctorAttributedAvailabilitySlot]
                if use_doctor_target and doctor_slots:
                    day_slots = self._filter_doctor_slots_by_preference(
                        doctor_slots,
                        preference=day_preference,
                    )
                elif attributed_slots:
                    day_slots = self._filter_attributed_slots_by_preference(
                        attributed_slots,
                        preference=day_preference,
                    )
                else:
                    day_slots = []

                if day_slots:
                    shown_day_slots = day_slots[:_MAX_RESCHEDULE_OFFERED_SLOTS]
                    offered_slots = self._serialize_reschedule_offered_slots(
                        shown_day_slots,
                        specialty_id=(
                            specialty_id_raw if isinstance(specialty_id_raw, str) else None
                        ),
                        specialty_name=specialty_name,
                        default_doctor_name=doctor_name,
                    )
                    clinic_tz = self.clinic_time_service.timezone
                    alternative_times = [
                        format_clinic_local_time_label(
                            self._reschedule_slot_start_time(slot),
                            clinic_tz,
                        )
                        for slot in shown_day_slots
                    ]
                    return RescheduleFlowResult(
                        intent="reschedule_request",
                        content=self._format_reschedule_exact_time_unavailable(
                            provider_label=provider_label,
                            requested_date=preference.requested_date,
                            exact_time=preference.exact_time,
                            alternative_times=alternative_times,
                        ),
                        chat_context_updates={
                            **base_updates,
                            "appointment_management_awaiting": (
                                APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
                            ),
                            "reschedule_offered_slots": offered_slots,
                            **(
                                {"reschedule_requested_date": preference.requested_date}
                                if preference.requested_date is not None
                                else {}
                            ),
                        },
                    )

                unavailable_content = self._format_reschedule_exact_time_unavailable(
                    provider_label=provider_label,
                    requested_date=preference.requested_date,
                    exact_time=preference.exact_time,
                )
            else:
                unavailable_content = RESCHEDULE_NO_AVAILABILITY_MESSAGE
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=unavailable_content,
                chat_context_updates=no_slot_updates,
            )

        if use_doctor_target:
            shown_doctor_slots = filtered_doctor_slots[:_MAX_RESCHEDULE_OFFERED_SLOTS]
            if preference.exact_time is not None and len(shown_doctor_slots) == 1:
                slot = shown_doctor_slots[0]
                offered_slots = self._serialize_reschedule_offered_slots(
                    [slot],
                    specialty_id=specialty_id_raw if isinstance(specialty_id_raw, str) else None,
                    specialty_name=specialty_name,
                    default_doctor_name=doctor_name,
                )
                display_time = format_clinic_local_time_label(
                    slot.start_time,
                    self.clinic_time_service.timezone,
                )
                return RescheduleFlowResult(
                    intent="reschedule_request",
                    content=self._format_reschedule_exact_time_available_hold_prompt(
                        provider_label=provider_label,
                        requested_date=preference.requested_date,
                        display_time=display_time,
                    ),
                    chat_context_updates={
                        **base_updates,
                        "appointment_management_awaiting": (
                            APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
                        ),
                        "reschedule_offered_slots": offered_slots,
                        **(
                            {"reschedule_requested_date": preference.requested_date}
                            if preference.requested_date is not None
                            else {}
                        ),
                    },
                )
            offered_slots = self._serialize_reschedule_offered_slots(
                shown_doctor_slots,
                specialty_id=specialty_id_raw if isinstance(specialty_id_raw, str) else None,
                specialty_name=specialty_name,
                default_doctor_name=doctor_name,
            )
        else:
            shown_attributed_slots = filtered_attributed_slots[:_MAX_RESCHEDULE_OFFERED_SLOTS]
            offered_slots = self._serialize_reschedule_offered_slots(
                shown_attributed_slots,
                specialty_id=specialty_id_raw if isinstance(specialty_id_raw, str) else None,
                specialty_name=specialty_name,
                default_doctor_name=doctor_name,
            )

        success_updates: dict[str, Any] = {
            **base_updates,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
            ),
            "reschedule_offered_slots": offered_slots,
        }
        if preference.requested_date is not None:
            success_updates["reschedule_requested_date"] = preference.requested_date
        if preference.time_window is not None:
            success_updates["reschedule_requested_time_window"] = preference.time_window

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=self._format_reschedule_offered_slots_reply(
                offered_slots,
                provider_label=provider_label,
            ),
            chat_context_updates=success_updates,
        )

    def handle_new_slot_selection(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        if is_appointment_selection_revision_message(
            message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            action_keywords=_RESCHEDULE_KEYWORDS,
        ):
            return self.revise_selected_appointment(
                message=message,
                chat_context=chat_context,
            )

        base_updates = self._new_slot_selection_context_updates(chat_context)

        selection = self.resolve_reschedule_slot_selection(
            message=message,
            chat_context=chat_context,
        )

        if selection.status is RescheduleSlotSelectionStatus.UNIQUE:
            assert selection.selected_slot is not None
            return self._create_reschedule_hold_and_request_confirmation(
                selected=selection.selected_slot,
                conversation=conversation,
                chat_context=chat_context,
            )

        if selection.status is RescheduleSlotSelectionStatus.AMBIGUOUS:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_NEW_SLOT_SELECTION_AMBIGUOUS,
                chat_context_updates=base_updates,
            )

        preference = self._extract_reschedule_preference(message)
        if not preference.requires_clarification:
            content = RESCHEDULE_NEW_SLOT_SELECTION_PREFERENCE_HINT
        else:
            content = RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=content,
            chat_context_updates=base_updates,
        )

    def handle_reschedule_confirmation(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        if is_appointment_selection_revision_message(
            message,
            chat_context=chat_context,
            clinic_timezone=self.clinic_time_service.timezone,
            clinic_today=self.clinic_time_service.clinic_today(),
            action_keywords=_RESCHEDULE_KEYWORDS,
        ):
            return self.revise_selected_appointment(
                message=message,
                chat_context=chat_context,
            )

        understanding = understand_confirmation(
            confirmation_type=ConfirmationType.RESCHEDULE_CONFIRMATION,
            message=message,
        )

        if understanding.decision is ConfirmationDecision.REJECTED:
            return self._handle_reschedule_rejection(
                conversation=conversation,
                chat_context=chat_context,
            )

        if understanding.decision in {
            ConfirmationDecision.UNCLEAR,
            ConfirmationDecision.WANTS_CHANGE,
        }:
            return self._reprompt_reschedule_confirmation(chat_context)

        return self._execute_reschedule_confirmation(
            conversation=conversation,
            chat_context=chat_context,
        )

    def _handle_reschedule_rejection(
        self,
        *,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        self._release_reschedule_hold_best_effort(
            conversation=conversation,
            chat_context=chat_context,
        )
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=RESCHEDULE_APPOINTMENT_REJECTED_MESSAGE,
            chat_context_updates=self._reschedule_confirmation_declined_context_updates(
                chat_context,
            ),
        )

    def _reprompt_reschedule_confirmation(
        self,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        original_summary = chat_context.get("selected_appointment_summary")
        new_slot_summary = self._resolve_selected_new_slot_summary(chat_context)
        if isinstance(original_summary, str) and new_slot_summary:
            confirmation_summary = _confirmation_summary_from_list_summary(original_summary)
            content = (
                f"Please confirm whether you want me to reschedule your "
                f"{confirmation_summary} to {new_slot_summary}."
            )
        else:
            content = RESCHEDULE_CONFIRMATION_REPROMPT_STUB

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=content,
            chat_context_updates=self._reschedule_confirmation_context_updates(chat_context),
        )

    def _execute_reschedule_confirmation(
        self,
        *,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        validation = self._validate_reschedule_confirmation_context(
            conversation=conversation,
            chat_context=chat_context,
        )
        if validation is not None:
            return validation

        selected_appointment_id = str(chat_context["selected_appointment_id"])
        selected_slot_id = str(chat_context["reschedule_selected_availability_slot_id"])
        hold_id = str(chat_context["reschedule_hold_id"])
        selected_summary = str(chat_context.get("selected_appointment_summary") or "")
        new_slot_summary = self._resolve_selected_new_slot_summary(chat_context) or (
            "the selected time"
        )
        confirmation_summary = _confirmation_summary_from_list_summary(selected_summary)

        appointment_id = UUID(selected_appointment_id)
        idempotency_key = (
            f"chat-reschedule:{conversation.id}:{selected_appointment_id}:{selected_slot_id}"
        )
        rescheduling_reason = chat_context.get("rescheduling_reason")
        request = AppointmentReschedulingRequest(
            appointment_id=appointment_id,
            explicit_confirmation=True,
            idempotency_key=idempotency_key,
            hold_id=UUID(hold_id),
            new_slot_id=UUID(selected_slot_id),
            owner_id=str(conversation.id),
            rescheduling_reason=(
                rescheduling_reason if isinstance(rescheduling_reason, str) else None
            ),
            source=CHAT_RESCHEDULE_SOURCE,
            actor_type=AuditActorType.CHAT,
            actor_id=str(conversation.id),
            conversation_id=str(conversation.id),
        )

        try:
            result = self.appointment_rescheduling.reschedule_appointment(request)
        except AppointmentReschedulingNotFoundError:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_OWNERSHIP_MISMATCH_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )
        except AppointmentReschedulingNotReschedulableError:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_NOT_RESCHEDULABLE_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )
        except AppointmentReschedulingHoldExpiredError:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_HOLD_EXPIRED_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_retry_slot_selection_context_updates(
                    chat_context,
                ),
            )
        except AppointmentReschedulingSlotUnavailableError:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_SLOT_UNAVAILABLE_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_retry_slot_selection_context_updates(
                    chat_context,
                ),
            )
        except Exception:
            logger.exception("Unexpected chat reschedule confirmation failure")
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_UNEXPECTED_FAILURE_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_context_updates(
                    chat_context,
                ),
            )

        content = (
            f"Your {confirmation_summary} has been rescheduled to {new_slot_summary}."
            f"{RESCHEDULE_SUCCESS_FOLLOW_UP_SUFFIX}"
        )
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=content,
            chat_context_updates=self._reschedule_confirmation_succeeded_context_updates(
                chat_context,
                original_appointment_id=selected_appointment_id,
                new_appointment_id=str(result.new_appointment_id),
                rescheduled_appointment_summary=new_slot_summary,
            ),
        )

    def _validate_reschedule_confirmation_context(
        self,
        *,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult | None:
        resolved_patient_id = chat_context.get("resolved_patient_id")
        selected_appointment_id = chat_context.get("selected_appointment_id")
        selected_slot_id = chat_context.get("reschedule_selected_availability_slot_id")
        hold_id = chat_context.get("reschedule_hold_id")
        hold_owner_id = chat_context.get("reschedule_hold_owner_id")

        if (
            not isinstance(resolved_patient_id, str)
            or not isinstance(selected_appointment_id, str)
            or not isinstance(selected_slot_id, str)
            or not isinstance(hold_id, str)
            or not isinstance(hold_owner_id, str)
        ):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_UNEXPECTED_FAILURE_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )

        if hold_owner_id != str(conversation.id):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_HOLD_EXPIRED_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_retry_slot_selection_context_updates(
                    chat_context,
                ),
            )

        try:
            appointment_id = UUID(selected_appointment_id)
            patient_id = UUID(resolved_patient_id)
            UUID(selected_slot_id)
            UUID(hold_id)
        except ValueError:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_OWNERSHIP_MISMATCH_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )

        appointment = self.appointments.get_by_id(appointment_id)
        if appointment is None:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_OWNERSHIP_MISMATCH_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )

        if appointment.patient_id != patient_id:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_OWNERSHIP_MISMATCH_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )

        if (
            not is_appointment_reschedulable(appointment.status)
            and appointment.status != AppointmentStatus.RESCHEDULED
        ):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_NOT_RESCHEDULABLE_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_completed_context_updates(
                    chat_context,
                ),
            )

        if appointment.status == AppointmentStatus.RESCHEDULED:
            return None

        hold = self.appointment_holds.get_hold_by_id(UUID(hold_id))
        if hold is None:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_HOLD_EXPIRED_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_retry_slot_selection_context_updates(
                    chat_context,
                ),
            )

        if str(hold.availability_slot_id) != selected_slot_id:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_HOLD_EXPIRED_MESSAGE,
                chat_context_updates=self._reschedule_confirmation_retry_slot_selection_context_updates(
                    chat_context,
                ),
            )

        return None

    def _resolve_selected_new_slot_summary(self, chat_context: dict[str, Any]) -> str | None:
        selected_slot_id = chat_context.get("reschedule_selected_availability_slot_id")
        if isinstance(selected_slot_id, str):
            offered = self._load_offered_reschedule_slot_views(chat_context)
            for slot in offered:
                if slot.availability_slot_id == selected_slot_id:
                    return slot.summary

        start_time = chat_context.get("reschedule_selected_start_time")
        doctor_name = chat_context.get("reschedule_selected_doctor_name")
        if isinstance(start_time, str):
            parsed_start = datetime.fromisoformat(start_time)
            clinic_tz = self.clinic_time_service.timezone
            summary = format_clinic_local_slot_summary(parsed_start, clinic_tz)
            if isinstance(doctor_name, str) and doctor_name.strip():
                return f"{summary} with {doctor_name.strip()}"
            return summary

        return None

    def _release_reschedule_hold_best_effort(
        self,
        *,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> None:
        hold_id = chat_context.get("reschedule_hold_id")
        hold_owner_id = chat_context.get("reschedule_hold_owner_id")
        if not isinstance(hold_id, str) or not isinstance(hold_owner_id, str):
            return
        if hold_owner_id != str(conversation.id):
            return

        try:
            self.appointment_holds.release_hold_by_id(
                hold_id=UUID(hold_id),
                owner_id=hold_owner_id,
            )
        except Exception:
            logger.exception("Failed to release reschedule hold on rejection")

    def _reschedule_confirmation_retry_slot_selection_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._new_slot_selection_context_updates(chat_context),
            **self._clear_stale_reschedule_slot_hold_state(),
        }

    def _reschedule_confirmation_declined_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._reschedule_confirmation_completed_context_updates(chat_context),
            "reschedule_status": "declined",
        }

    def _reschedule_confirmation_succeeded_context_updates(
        self,
        chat_context: dict[str, Any],
        *,
        original_appointment_id: str,
        new_appointment_id: str,
        rescheduled_appointment_summary: str,
    ) -> dict[str, Any]:
        return {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
            "reschedule_status": "rescheduled",
            "rescheduled_from_appointment_id": original_appointment_id,
            "new_appointment_id": new_appointment_id,
            "rescheduled_appointment_summary": rescheduled_appointment_summary,
            **self._resolved_patient_context(chat_context),
            "selected_appointment_id": None,
            "selected_appointment_summary": None,
            "offered_appointments": None,
            **self._clear_stale_reschedule_slot_hold_state(),
        }

    def _reschedule_confirmation_completed_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
            **self._resolved_patient_context(chat_context),
            "selected_appointment_id": None,
            "selected_appointment_summary": None,
            "offered_appointments": None,
            **self._clear_stale_reschedule_slot_hold_state(),
        }

    def resolve_reschedule_slot_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleSlotSelectionResult:
        offered = self._load_offered_reschedule_slot_views(chat_context)
        if not offered:
            return RescheduleSlotSelectionResult(
                status=RescheduleSlotSelectionStatus.ZERO,
            )

        return self._resolve_offered_reschedule_slot_selection(
            message=message,
            offered=offered,
        )

    def _extract_reschedule_preference(
        self,
        message: str,
    ) -> _ReschedulePreferenceExtraction:
        soonest_requested = _detect_soonest_request(message)
        availability_range = _extract_reschedule_availability_range(message)
        date_extraction = self._extract_reschedule_date(message)
        time_extraction = self._extract_reschedule_time_window(message)
        exact_time = self._extract_reschedule_exact_time(message)

        if date_extraction.requires_clarification or time_extraction.requires_clarification:
            return _ReschedulePreferenceExtraction(requires_clarification=True)

        if availability_range is not None:
            week_range = self._resolve_reschedule_week_range(availability_range)
            if week_range is None:
                return _ReschedulePreferenceExtraction(requires_clarification=True)
            start_date, end_date = week_range
            return _ReschedulePreferenceExtraction(
                search_start_date=start_date.isoformat(),
                search_end_date=end_date.isoformat(),
                time_window=time_extraction.window,
                exact_time=exact_time,
            )

        if soonest_requested and date_extraction.normalized_date is None:
            clinic_today = self.clinic_time_service.clinic_today()
            return _ReschedulePreferenceExtraction(
                search_start_date=clinic_today.isoformat(),
                search_end_date=(
                    clinic_today + timedelta(days=EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS)
                ).isoformat(),
                time_window=time_extraction.window,
                exact_time=exact_time,
            )

        if date_extraction.normalized_date is not None:
            return _ReschedulePreferenceExtraction(
                requested_date=date_extraction.normalized_date,
                time_window=time_extraction.window,
                exact_time=exact_time,
            )

        return _ReschedulePreferenceExtraction(requires_clarification=True)

    def _extract_reschedule_date(self, message: str) -> _RescheduleDateExtraction:
        parse_result = self.date_parser.parse(message)
        if (
            parse_result.status == DateParseStatus.PARSED
            and parse_result.normalized_date is not None
        ):
            return _RescheduleDateExtraction(
                normalized_date=parse_result.normalized_date,
            )

        if parse_result.status in {
            DateParseStatus.AMBIGUOUS,
            DateParseStatus.INVALID,
        }:
            return _RescheduleDateExtraction(requires_clarification=True)

        stripped_message = _strip_time_preference_markers(message)
        if stripped_message != message:
            retry_result = self.date_parser.parse(stripped_message)
            if (
                retry_result.status == DateParseStatus.PARSED
                and retry_result.normalized_date is not None
            ):
                return _RescheduleDateExtraction(
                    normalized_date=retry_result.normalized_date,
                )
            if retry_result.status in {
                DateParseStatus.AMBIGUOUS,
                DateParseStatus.INVALID,
            }:
                return _RescheduleDateExtraction(requires_clarification=True)

        bare_weekday = self._parse_bare_weekday(stripped_message or message)
        if bare_weekday is not None:
            return _RescheduleDateExtraction(normalized_date=bare_weekday)

        if parse_result.status == DateParseStatus.UNSUPPORTED:
            return _RescheduleDateExtraction()

        return _RescheduleDateExtraction()

    def _extract_reschedule_time_window(
        self,
        message: str,
    ) -> _RescheduleTimeExtraction:
        parse_result = self.time_preference_parser.parse(message)
        if parse_result.status == TimePreferenceStatus.PARSED and parse_result.window is not None:
            window = parse_result.window
            return _RescheduleTimeExtraction(
                window={
                    "label": window.label,
                    "start_time": window.start_time,
                    "end_time": window.end_time,
                },
            )

        if parse_result.status == TimePreferenceStatus.NOT_FOUND:
            return _RescheduleTimeExtraction()

        if parse_result.status == TimePreferenceStatus.AMBIGUOUS:
            return _RescheduleTimeExtraction(requires_clarification=True)

        return _RescheduleTimeExtraction()

    def _extract_reschedule_exact_time(self, message: str) -> str | None:
        normalized_time = normalize_appointment_time_expression(
            message,
            allow_bare_hour=False,
        )
        if normalized_time is None:
            return None
        return normalized_time.value

    def _parse_bare_weekday(self, message: str) -> str | None:
        normalized = message.lower()
        if _PREFIXED_WEEKDAY_PATTERN.search(normalized):
            return None

        match = _BARE_WEEKDAY_PATTERN.search(normalized)
        if match is None:
            return None

        weekday_index = _WEEKDAY_TO_INDEX[match.group(1).lower()]
        today = self.clinic_time_service.clinic_today()
        days_ahead = (weekday_index - today.weekday()) % 7
        return (today + timedelta(days=days_ahead)).isoformat()

    def _resolve_reschedule_week_range(
        self,
        label: str,
    ) -> tuple[date, date] | None:
        today = self.clinic_time_service.clinic_today()
        start_of_week = today - timedelta(days=today.weekday())

        if label == _RESCHEDULE_AVAILABILITY_RANGE_NEXT_WEEK:
            start = start_of_week + timedelta(days=7)
        elif label == _RESCHEDULE_AVAILABILITY_RANGE_THIS_WEEK:
            start = start_of_week
        else:
            return None

        end = start + timedelta(days=6)
        if start < today:
            start = today
        return start, end

    def _search_doctor_availability(
        self,
        *,
        doctor_id: UUID,
        preference: _ReschedulePreferenceExtraction,
    ) -> Sequence[AvailabilitySlot]:
        if preference.requested_date is not None:
            return self._query_doctor_availability_for_date(
                doctor_id=doctor_id,
                requested_date=date.fromisoformat(preference.requested_date),
            )

        if preference.search_start_date is not None and preference.search_end_date is not None:
            start_from, start_to = self._build_availability_range_datetimes(
                start_date=date.fromisoformat(preference.search_start_date),
                end_date=date.fromisoformat(preference.search_end_date),
            )
            return self.scheduling.check_availability(
                doctor_id=doctor_id,
                start_from=start_from,
                start_to=start_to,
            )

        return []

    def _search_specialty_availability(
        self,
        *,
        specialty_id: UUID,
        preference: _ReschedulePreferenceExtraction,
    ) -> Sequence[DoctorAttributedAvailabilitySlot]:
        if preference.requested_date is not None:
            start_from, start_to = self._build_availability_range_datetimes(
                start_date=date.fromisoformat(preference.requested_date),
                end_date=date.fromisoformat(preference.requested_date),
            )
        elif preference.search_start_date is not None and preference.search_end_date is not None:
            start_from, start_to = self._build_availability_range_datetimes(
                start_date=date.fromisoformat(preference.search_start_date),
                end_date=date.fromisoformat(preference.search_end_date),
            )
        else:
            return []

        result = self.scheduling.check_availability_for_specialty(
            specialty_id=specialty_id,
            start_from=start_from,
            start_to=start_to,
            limit=_MAX_RESCHEDULE_OFFERED_SLOTS,
        )
        return result.available_slots

    def _query_doctor_availability_for_date(
        self,
        *,
        doctor_id: UUID,
        requested_date: date,
    ) -> Sequence[AvailabilitySlot]:
        availability_window = SchedulingAvailabilityResolver(
            self.clinic_time_service,
            date_parser=self.date_parser,
        ).resolve_check_availability(
            CheckAvailabilityToolArguments(
                date_expression=DateExpressionSchema(
                    kind=DateExpressionKind.EXACT_DATE,
                    exact_date=requested_date,
                ),
            ),
            {},
        )
        if not availability_window.is_resolved:
            return []

        assert availability_window.start_from is not None
        assert availability_window.end_to is not None
        return self.scheduling.check_availability(
            doctor_id=doctor_id,
            start_from=availability_window.start_from,
            start_to=availability_window.end_to,
        )

    def _build_availability_range_datetimes(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[datetime, datetime]:
        start_from = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
        start_to = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(
            days=1,
        )
        return start_from, start_to

    def _filter_doctor_slots_by_preference(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        preference: _ReschedulePreferenceExtraction,
    ) -> list[AvailabilitySlot]:
        filtered = list(slots)
        if preference.time_window is not None:
            filtered = self._filter_slots_by_time_window(filtered, preference.time_window)
        if preference.exact_time is not None:
            clinic_tz = self.clinic_time_service.timezone
            filtered = [
                slot
                for slot in filtered
                if format_clinic_local_time_label(slot.start_time, clinic_tz)
                == preference.exact_time
            ]
        return filtered

    def _filter_attributed_slots_by_preference(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        *,
        preference: _ReschedulePreferenceExtraction,
    ) -> list[DoctorAttributedAvailabilitySlot]:
        filtered = list(slots)
        if preference.time_window is not None:
            filtered = self._filter_attributed_slots_by_time_window(
                filtered,
                preference.time_window,
            )
        if preference.exact_time is not None:
            clinic_tz = self.clinic_time_service.timezone
            filtered = [
                item
                for item in filtered
                if format_clinic_local_time_label(item.slot.start_time, clinic_tz)
                == preference.exact_time
            ]
        return filtered

    def _filter_slots_by_time_window(
        self,
        slots: Sequence[AvailabilitySlot],
        window: dict[str, str],
    ) -> list[AvailabilitySlot]:
        label = window.get("label")
        start_time = window.get("start_time")
        end_time = window.get("end_time")
        if (
            not isinstance(label, str)
            or not isinstance(start_time, str)
            or not isinstance(end_time, str)
        ):
            return list(slots)

        time_window = TimeWindow(
            label=label,
            start_time=start_time,
            end_time=end_time,
        )
        clinic_tz = self.clinic_time_service.timezone
        return [
            slot
            for slot in slots
            if is_time_in_window(
                time_value=format_clinic_local_time_label(slot.start_time, clinic_tz),
                window=time_window,
            )
        ]

    def _filter_attributed_slots_by_time_window(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        window: dict[str, str],
    ) -> list[DoctorAttributedAvailabilitySlot]:
        label = window.get("label")
        start_time = window.get("start_time")
        end_time = window.get("end_time")
        if (
            not isinstance(label, str)
            or not isinstance(start_time, str)
            or not isinstance(end_time, str)
        ):
            return list(slots)

        time_window = TimeWindow(
            label=label,
            start_time=start_time,
            end_time=end_time,
        )
        clinic_tz = self.clinic_time_service.timezone
        return [
            item
            for item in slots
            if is_time_in_window(
                time_value=format_clinic_local_time_label(
                    item.slot.start_time,
                    clinic_tz,
                ),
                window=time_window,
            )
        ]

    def _reschedule_slot_start_time(
        self,
        slot: AvailabilitySlot | DoctorAttributedAvailabilitySlot,
    ) -> datetime:
        if isinstance(slot, DoctorAttributedAvailabilitySlot):
            return slot.slot.start_time
        return slot.start_time

    def _serialize_reschedule_offered_slots(
        self,
        slots: Sequence[AvailabilitySlot] | Sequence[DoctorAttributedAvailabilitySlot],
        *,
        specialty_id: str | None,
        specialty_name: str | None,
        default_doctor_name: str | None,
    ) -> list[dict[str, str]]:
        offered: list[dict[str, str]] = []
        for item in slots:
            if isinstance(item, DoctorAttributedAvailabilitySlot):
                slot = item.slot
                doctor_id = str(item.doctor_id)
                doctor_name = item.doctor_name
            elif isinstance(item, AvailabilitySlot):
                slot = item
                doctor_id = str(slot.doctor_id)
                doctor_name = default_doctor_name or ""
            else:
                continue

            summary = format_clinic_local_slot_summary(
                slot.start_time,
                self.clinic_time_service.timezone,
            )

            entry: dict[str, str] = {
                "availability_slot_id": str(slot.id),
                "doctor_id": doctor_id,
                "doctor_name": doctor_name,
                "start_time": slot.start_time.isoformat(),
                "summary": summary,
            }
            if specialty_id is not None:
                entry["specialty_id"] = specialty_id
            if specialty_name is not None:
                entry["specialty_name"] = specialty_name
            offered.append(entry)

        return offered

    def _format_reschedule_offered_slots_reply(
        self,
        offered_slots: Sequence[dict[str, str]],
        *,
        provider_label: str,
    ) -> str:
        if not offered_slots:
            return RESCHEDULE_NO_AVAILABILITY_MESSAGE

        lines = [
            f"{index}. {slot['summary']}."
            for index, slot in enumerate(offered_slots, start=1)
        ]
        options = "\n".join(lines)
        return (
            f"I found these openings with {provider_label}:\n\n"
            f"{options}\n"
            "Which time would you like?"
        )

    def _format_reschedule_weekday_label(self, requested_date: str | None) -> str:
        if requested_date is None:
            return "that day"
        try:
            return date.fromisoformat(requested_date).strftime("%A")
        except ValueError:
            return requested_date

    def _format_reschedule_exact_time_available_hold_prompt(
        self,
        *,
        provider_label: str,
        requested_date: str | None,
        display_time: str,
    ) -> str:
        weekday = self._format_reschedule_weekday_label(requested_date)
        return (
            f"Yes, {provider_label} is available on {weekday} at {display_time}. "
            "Would you like me to hold that time?"
        )

    def _format_reschedule_exact_time_unavailable(
        self,
        *,
        provider_label: str,
        requested_date: str | None,
        exact_time: str,
        alternative_times: Sequence[str] | None = None,
    ) -> str:
        weekday = self._format_reschedule_weekday_label(requested_date)
        if alternative_times:
            alternatives_text = ", ".join(alternative_times)
            return (
                f"{exact_time} is not available with {provider_label} on {weekday}, "
                f"but I found these openings that day: {alternatives_text}. "
                "Which time would you like?"
            )
        return (
            f"I don't have {exact_time} available with {provider_label} on {weekday}. "
            "Would you like me to check another day or time window?"
        )

    def _reschedule_flow_context(self, chat_context: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {
            **self._resolved_patient_context(chat_context),
            **self._preserved_offered_appointments(chat_context),
        }
        for key in (
            "selected_appointment_id",
            "selected_appointment_summary",
            "selected_appointment_doctor_id",
            "selected_appointment_doctor_name",
            "selected_appointment_specialty_id",
            "selected_appointment_specialty_name",
            "selected_appointment_start_time",
        ):
            if key in chat_context:
                updates[key] = chat_context[key]
        return updates

    def _clear_stale_reschedule_slot_hold_state(self) -> dict[str, Any]:
        return {
            "reschedule_offered_slots": None,
            "reschedule_selected_availability_slot_id": None,
            "reschedule_selected_start_time": None,
            "reschedule_selected_doctor_id": None,
            "reschedule_selected_doctor_name": None,
            "reschedule_selected_specialty_id": None,
            "reschedule_selected_specialty_name": None,
            "reschedule_hold_id": None,
            "reschedule_hold_expires_at": None,
            "reschedule_hold_owner_id": None,
        }

    def _new_slot_selection_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._reschedule_flow_context(chat_context),
            **self._preserved_reschedule_slot_context(chat_context),
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
            ),
        }

    def _reschedule_confirmation_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._reschedule_flow_context(chat_context),
            **self._preserved_reschedule_slot_context(chat_context),
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
            ),
        }

    def _preserved_reschedule_slot_context(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if chat_context.get("reschedule_offered_slots") is not None:
            updates["reschedule_offered_slots"] = chat_context["reschedule_offered_slots"]
        for key in (
            "reschedule_selected_availability_slot_id",
            "reschedule_selected_start_time",
            "reschedule_selected_doctor_id",
            "reschedule_selected_doctor_name",
            "reschedule_selected_specialty_id",
            "reschedule_selected_specialty_name",
            "reschedule_hold_id",
            "reschedule_hold_expires_at",
            "reschedule_hold_owner_id",
        ):
            if key in chat_context:
                updates[key] = chat_context[key]
        return updates

    def _create_reschedule_hold_and_request_confirmation(
        self,
        *,
        selected: _OfferedRescheduleSlotView,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        owner_id = str(conversation.id)
        slot_selection_updates = self._new_slot_selection_context_updates(chat_context)

        try:
            slot = self.scheduling.get_available_slot_for_hold(
                UUID(selected.availability_slot_id),
            )
            hold = self.appointment_holds.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
                ttl_seconds=self._chat_appointment_hold_ttl_seconds,
            )
        except (
            AvailabilitySlotNotFoundError,
            AvailabilitySlotUnavailableError,
            AppointmentSlotAlreadyHeldError,
            AppointmentHoldStoreUnavailableError,
        ):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_HOLD_UNAVAILABLE_MESSAGE,
                chat_context_updates=slot_selection_updates,
            )

        hold_expires_at = hold.created_at + timedelta(
            seconds=self._chat_appointment_hold_ttl_seconds,
        )
        original_summary = str(chat_context.get("selected_appointment_summary") or "")
        confirmation_content = self._format_reschedule_confirmation_prompt(
            original_summary=original_summary,
            new_slot_summary=selected.summary,
        )
        hold_updates: dict[str, Any] = {
            "reschedule_selected_availability_slot_id": str(slot.id),
            "reschedule_selected_start_time": slot.start_time.isoformat(),
            "reschedule_selected_doctor_id": str(slot.doctor_id),
            "reschedule_selected_doctor_name": selected.doctor_name,
            "reschedule_hold_id": str(hold.hold_id),
            "reschedule_hold_expires_at": hold_expires_at.isoformat(),
            "reschedule_hold_owner_id": owner_id,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
            ),
        }
        if selected.specialty_id is not None:
            hold_updates["reschedule_selected_specialty_id"] = selected.specialty_id
        if selected.specialty_name:
            hold_updates["reschedule_selected_specialty_name"] = selected.specialty_name

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=confirmation_content,
            chat_context_updates={
                **slot_selection_updates,
                **hold_updates,
            },
        )

    def _format_reschedule_confirmation_prompt(
        self,
        *,
        original_summary: str,
        new_slot_summary: str,
    ) -> str:
        confirmation_summary = _confirmation_summary_from_list_summary(original_summary)
        return (
            f"Please confirm: should I reschedule your {confirmation_summary} "
            f"to {new_slot_summary}?"
        )

    def _load_offered_reschedule_slot_views(
        self,
        chat_context: dict[str, Any],
    ) -> list[_OfferedRescheduleSlotView]:
        raw_offered = chat_context.get("reschedule_offered_slots")
        if not isinstance(raw_offered, list):
            return []

        offered: list[_OfferedRescheduleSlotView] = []
        for item in raw_offered:
            if not isinstance(item, dict):
                continue
            availability_slot_id = item.get("availability_slot_id")
            start_time = item.get("start_time")
            if not isinstance(availability_slot_id, str) or not isinstance(
                start_time,
                str,
            ):
                continue

            parsed_start = datetime.fromisoformat(start_time)
            clinic_tz = self.clinic_time_service.timezone
            localized = to_clinic_local_datetime(parsed_start, clinic_tz)
            weekday = localized.strftime("%A")
            time_label = format_clinic_local_time_label(parsed_start, clinic_tz)
            summary = item.get("summary")
            if not isinstance(summary, str) or not summary:
                summary = format_clinic_local_slot_summary(parsed_start, clinic_tz)

            offered.append(
                _OfferedRescheduleSlotView(
                    availability_slot_id=availability_slot_id,
                    summary=summary,
                    weekday=weekday,
                    time_label=time_label,
                    doctor_id=_optional_str(item.get("doctor_id")),
                    doctor_name=str(item.get("doctor_name") or ""),
                    specialty_id=_optional_str(item.get("specialty_id")),
                    specialty_name=str(item.get("specialty_name") or ""),
                    start_time=start_time,
                ),
            )
        return offered

    def _resolve_offered_reschedule_slot_selection(
        self,
        *,
        message: str,
        offered: Sequence[_OfferedRescheduleSlotView],
    ) -> RescheduleSlotSelectionResult:
        normalized_message = message.lower().strip()
        candidate_indices = list(range(len(offered)))
        signals_detected = False

        option_index = _extract_option_number_index(
            normalized_message,
            option_count=len(offered),
        )
        if option_index is not None:
            signals_detected = True
            candidate_indices = [
                index for index in candidate_indices if index == option_index
            ]

        ordinal_index = _extract_ordinal_index(
            normalized_message,
            option_count=len(offered),
        )
        if ordinal_index is not None:
            signals_detected = True
            candidate_indices = [
                index for index in candidate_indices if index == ordinal_index
            ]

        specialty_query = _extract_reschedule_slot_specialty_query(
            normalized_message,
            offered=offered,
        )
        if specialty_query is not None:
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if specialty_query in offered[index].specialty_name.lower()
            ]

        if _any_reschedule_slot_doctor_mentioned(normalized_message, offered):
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if doctor_name_in_message(
                    normalized_message,
                    offered[index].doctor_name,
                )
            ]

        if _any_reschedule_slot_weekday_mentioned(normalized_message, offered):
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if offered[index].weekday.lower() in normalized_message
            ]

        normalized_time = normalize_appointment_time_expression(
            message,
            allow_bare_hour=bool(offered),
        )
        if normalized_time is not None:
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if offered[index].time_label == normalized_time.value
            ]

        iso_datetime = _extract_iso_datetime(message)
        if iso_datetime is not None:
            signals_detected = True
            iso_value = iso_datetime.isoformat()
            candidate_indices = [
                index
                for index in candidate_indices
                if offered[index].start_time == iso_value
            ]

        if not signals_detected:
            return RescheduleSlotSelectionResult(
                status=RescheduleSlotSelectionStatus.ZERO,
            )

        if len(candidate_indices) == 1:
            return RescheduleSlotSelectionResult(
                status=RescheduleSlotSelectionStatus.UNIQUE,
                selected_slot=offered[candidate_indices[0]],
            )

        if not candidate_indices:
            return RescheduleSlotSelectionResult(
                status=RescheduleSlotSelectionStatus.ZERO,
            )

        return RescheduleSlotSelectionResult(
            status=RescheduleSlotSelectionStatus.AMBIGUOUS,
        )

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
            conversation_state=ConversationState.RESCHEDULE_INTAKE,
            expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
            latest_user_message=message,
            allowed_intents=[ChatTurnIntent.PATIENT_IDENTITY_PROVIDED],
            current_context={
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
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
                "chat turn understanding interpreter failed during reschedule identity intake",
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

    def _resolve_doctor_name(self, doctor_id: UUID) -> str:
        for doctor in self.scheduling_metadata.list_doctors():
            if doctor.id == doctor_id:
                return doctor.full_name
        return "your doctor"

    def _resolve_specialty_name(self, specialty_id: UUID) -> str:
        for specialty in self.scheduling_metadata.list_specialties():
            if specialty.id == specialty_id:
                return specialty.name
        return "appointment"

    def _resolve_single_offered_appointment_response(
        self,
        *,
        message: str,
        offered: Sequence[OfferedAppointmentView],
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult | None:
        if _is_single_appointment_rejection(message):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_REJECTED_MESSAGE,
                chat_context_updates=self._reschedule_declined_context_updates(),
            )

        if _is_single_appointment_confirmation(message):
            return self._build_appointment_selected_result(
                selected=offered[0],
                chat_context=chat_context,
            )

        return None

    def _build_appointment_selected_result(
        self,
        *,
        selected: OfferedAppointmentView,
        chat_context: dict[str, Any],
        clear_pending_refinement: bool = False,
    ) -> RescheduleFlowResult:
        pending_clear = (
            clear_pending_selection_context_updates()
            if clear_pending_refinement
            else {}
        )
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=_new_time_preference_prompt(selected.summary),
            chat_context_updates={
                **self._resolved_patient_context(chat_context),
                **pending_clear,
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
                ),
                **self._selected_appointment_context(selected),
                **self._preserved_offered_appointments(chat_context),
            },
        )

    def _offered_view_from_selection(
        self,
        selected: dict[str, str],
        offered: Sequence[OfferedAppointmentView],
    ) -> OfferedAppointmentView | None:
        for item in offered:
            if item.appointment_id == selected["appointment_id"]:
                return item
        return None

    def _appointment_selection_context_updates(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        updates = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
            ),
            **self._resolved_patient_context(chat_context),
            **self._preserved_offered_appointments(chat_context),
        }
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

    def _preserved_offered_appointments(
        self,
        chat_context: dict[str, Any],
    ) -> dict[str, Any]:
        if chat_context.get("offered_appointments") is not None:
            return {"offered_appointments": chat_context["offered_appointments"]}
        return {}

    def _selected_appointment_context(
        self,
        selected: OfferedAppointmentView,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "selected_appointment_id": selected.appointment_id,
            "selected_appointment_summary": selected.summary,
        }
        if selected.doctor_id is not None:
            updates["selected_appointment_doctor_id"] = selected.doctor_id
        if selected.doctor_name:
            updates["selected_appointment_doctor_name"] = selected.doctor_name
        if selected.specialty_id is not None:
            updates["selected_appointment_specialty_id"] = selected.specialty_id
        if selected.specialty_name:
            updates["selected_appointment_specialty_name"] = selected.specialty_name
        if selected.start_time is not None:
            updates["selected_appointment_start_time"] = selected.start_time
        return updates

    def _reschedule_declined_context_updates(self) -> dict[str, Any]:
        return {
            "appointment_management_mode": None,
            "appointment_management_awaiting": None,
            "offered_appointments": None,
            "selected_appointment_id": None,
            "selected_appointment_summary": None,
        }


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _detect_soonest_request(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in _SOONEST_MARKERS)


def _extract_reschedule_availability_range(message: str) -> str | None:
    match = _AVAILABILITY_RANGE_PATTERN.search(message)
    if match is None:
        return None
    if match.group(1).lower() == "next":
        return _RESCHEDULE_AVAILABILITY_RANGE_NEXT_WEEK
    return _RESCHEDULE_AVAILABILITY_RANGE_THIS_WEEK


def _strip_time_preference_markers(message: str) -> str:
    stripped = message
    for label in ("morning", "afternoon", "evening"):
        stripped = re.sub(rf"\b{label}\b", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\bor\b", " ", stripped, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", stripped).strip()


def _message_contains_reschedule_keyword(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in _RESCHEDULE_KEYWORDS)


def _normalize_phrase_message(message: str) -> str:
    collapsed = re.sub(r"\s+", " ", message.strip().lower())
    if not collapsed:
        return collapsed
    return _TRAILING_PUNCTUATION.sub("", collapsed).strip()


def _matches_extra_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    ordered = sorted(phrases, key=len, reverse=True)
    for phrase in ordered:
        if normalized == phrase:
            return True
        if normalized.startswith(f"{phrase} "):
            return True
        if normalized.startswith(f"{phrase},"):
            return True
        if normalized.startswith(f"{phrase}."):
            return True
        if normalized.startswith(f"{phrase}!"):
            return True
    return False


def _is_single_appointment_confirmation(message: str) -> bool:
    understanding = understand_confirmation(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message=message,
    )
    if understanding.decision is ConfirmationDecision.CONFIRMED:
        return True
    normalized = _normalize_phrase_message(message)
    return _matches_extra_phrase(normalized, _RESCHEDULE_SINGLE_CONFIRMED_PHRASES)


def _is_single_appointment_rejection(message: str) -> bool:
    understanding = understand_confirmation(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message=message,
    )
    if understanding.decision is ConfirmationDecision.REJECTED:
        return True
    normalized = _normalize_phrase_message(message)
    return _matches_extra_phrase(normalized, _RESCHEDULE_SINGLE_REJECTED_PHRASES)


def _new_time_preference_prompt(summary: str) -> str:
    confirmation_summary = _confirmation_summary_from_list_summary(summary)
    return (
        f"Got it. What day or time would you prefer instead for your "
        f"{confirmation_summary}?"
    )


def _extract_reschedule_slot_specialty_query(
    normalized_message: str,
    *,
    offered: Sequence[_OfferedRescheduleSlotView],
) -> str | None:
    match = _SPECIALTY_SELECTION_PATTERN.search(normalized_message)
    if match is not None:
        specialty_query = match.group(1).strip().lower()
        if specialty_query not in _ORDINAL_WORDS:
            return specialty_query

    for item in offered:
        specialty = item.specialty_name.lower()
        if specialty and re.search(rf"\b{re.escape(specialty)}\b", normalized_message):
            return specialty

    return None


def _any_reschedule_slot_doctor_mentioned(
    normalized_message: str,
    offered: Sequence[_OfferedRescheduleSlotView],
) -> bool:
    return any(
        doctor_name_in_message(normalized_message, item.doctor_name)
        for item in offered
        if item.doctor_name
    )


def _any_reschedule_slot_weekday_mentioned(
    normalized_message: str,
    offered: Sequence[_OfferedRescheduleSlotView],
) -> bool:
    return any(item.weekday.lower() in normalized_message for item in offered)


def _extract_iso_datetime(message: str) -> datetime | None:
    match = _ISO_DATETIME_PATTERN.search(message)
    if match is None:
        return None

    raw_value = match.group(1).replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed
