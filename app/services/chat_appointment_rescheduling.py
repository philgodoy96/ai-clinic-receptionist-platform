from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

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
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
)
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    _appointment_count_label,
    _confirmation_summary_from_list_summary,
    _extract_option_number_index,
    _extract_ordinal_index,
    _parse_offered_appointment_summary,
)
from app.services.chat_booking_identity import (
    ParsedPatientFields,
    _dob_ambiguity_issue,
    _is_valid_iso_date,
    _merge_parsed_fields,
)
from app.services.chat_confirmation import (
    ConfirmationDecision,
    ConfirmationType,
    normalize_patient_display_name,
    understand_confirmation,
)
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.clinic_time import ClinicTimeService
from app.services.patient_identity_resolution import PatientIdentityResolutionService

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE = "reschedule"
APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE = "new_time_preference"

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
RESCHEDULE_PATIENT_NOT_FOUND_MESSAGE = (
    "I couldn't find a matching patient profile with that name and date of birth. "
    "Could you check the details and try again?"
)
RESCHEDULE_NO_UPCOMING_APPOINTMENTS_MESSAGE = (
    "I'm not seeing any upcoming appointments that can be rescheduled for that patient."
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


@dataclass(frozen=True, slots=True)
class RescheduleFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


class RescheduleAppointmentSelectionStatus(StrEnum):
    UNIQUE = "unique"
    ZERO = "zero"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class RescheduleAppointmentSelectionResult:
    status: RescheduleAppointmentSelectionStatus
    selected_appointment: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class _OfferedAppointmentView:
    appointment_id: str
    summary: str
    specialty_name: str
    doctor_name: str
    weekday: str
    time_label: str
    doctor_id: str | None = None
    specialty_id: str | None = None
    start_time: str | None = None


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
        scheduling_metadata: SchedulingMetadataForRescheduling,
        clinic_time_service: ClinicTimeService,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
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
    ) -> RescheduleFlowResult:
        base_updates = {
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
        if dob_issue is not None:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=dob_issue.clarification_question or (
                    "Please clarify the patient's date of birth."
                ),
                chat_context_updates=base_updates,
            )

        if parsed.full_name is None or parsed.date_of_birth is None:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
                chat_context_updates=base_updates,
            )

        full_name = normalize_patient_display_name(parsed.full_name)
        resolution = self.patient_identity_resolution.resolve(
            PatientIdentityResolutionRequest(
                patient_name=full_name,
                patient_date_of_birth=date.fromisoformat(parsed.date_of_birth),
                conversation_id=conversation.id,
                patient_email=parsed.email,
                patient_phone=parsed.phone,
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
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates=base_updates,
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
        start_from = self.clinic_time_service.clinic_now()
        reschedulable = self.appointments.list_reschedulable_for_patient(
            patient_id=record.patient_id,
            start_from=start_from,
        )

        if not reschedulable:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_NO_UPCOMING_APPOINTMENTS_MESSAGE,
                chat_context_updates=base_updates,
            )

        presentations = [
            self._present_appointment(appointment) for appointment in reschedulable
        ]
        shared_context = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
            ),
            "resolved_patient_id": str(record.patient_id),
            "resolved_patient_name": resolved_name,
            "patient_resolution_id": resolution.patient_resolution_id,
            "offered_appointments": [
                self._offered_appointment_entry(
                    appointment=appointment,
                    presentation=presentation,
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

    def handle_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        base_updates = self._appointment_selection_context_updates(chat_context)

        if _message_contains_reschedule_keyword(message):
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_SELECTION_REPROMPT,
                chat_context_updates=base_updates,
            )

        offered = self._load_offered_appointment_views(chat_context)
        if len(offered) == 1:
            single_result = self._resolve_single_offered_appointment_response(
                message=message,
                offered=offered,
                chat_context=chat_context,
            )
            if single_result is not None:
                return single_result

        selection = self.resolve_appointment_selection(
            message=message,
            chat_context=chat_context,
        )

        if selection.status is RescheduleAppointmentSelectionStatus.UNIQUE:
            selected = selection.selected_appointment
            assert selected is not None
            selected_view = self._offered_view_from_selection(selected, offered)
            if selected_view is None:
                return RescheduleFlowResult(
                    intent="reschedule_request",
                    content=RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH,
                    chat_context_updates=base_updates,
                )
            return self._build_appointment_selected_result(
                selected=selected_view,
                chat_context=chat_context,
            )

        if selection.status is RescheduleAppointmentSelectionStatus.AMBIGUOUS:
            return RescheduleFlowResult(
                intent="reschedule_request",
                content=RESCHEDULE_APPOINTMENT_SELECTION_AMBIGUOUS,
                chat_context_updates=base_updates,
            )

        return RescheduleFlowResult(
            intent="reschedule_request",
            content=RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH,
            chat_context_updates=base_updates,
        )

    def resolve_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleAppointmentSelectionResult:
        offered = self._load_offered_appointment_views(chat_context)
        if not offered:
            return RescheduleAppointmentSelectionResult(
                status=RescheduleAppointmentSelectionStatus.ZERO,
            )

        return self._resolve_offered_appointment_selection(
            message=message,
            offered=offered,
        )

    def _resolve_patient_fields(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
        parse_patient_fields: Callable[..., ParsedPatientFields],
    ) -> tuple[ParsedPatientFields, FieldIssue | None]:
        deterministic = parse_patient_fields(message, booking_context=True)
        understanding = self._interpret_identity_turn(
            message=message,
            chat_context=chat_context,
        )
        if understanding is None or self._should_use_deterministic_only(understanding):
            return deterministic, None

        ctu_fields, dob_issue = self._validated_fields_from_understanding(understanding)
        merged = _merge_parsed_fields(deterministic, ctu_fields)
        if deterministic.phone and not merged.phone:
            merged = ParsedPatientFields(
                full_name=merged.full_name,
                date_of_birth=merged.date_of_birth,
                email=merged.email,
                phone=deterministic.phone,
            )
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
        localized_start = appointment.start_time.astimezone(self.clinic_time_service.timezone)
        weekday = localized_start.strftime("%A")
        time_label = localized_start.strftime("%H:%M")

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

    def _offered_appointment_entry(
        self,
        *,
        appointment: Appointment,
        presentation: _AppointmentPresentation,
    ) -> dict[str, str]:
        return {
            "appointment_id": str(presentation.appointment_id),
            "summary": presentation.list_summary,
            "doctor_id": str(appointment.doctor_id),
            "doctor_name": presentation.doctor_name,
            "specialty_id": str(appointment.specialty_id),
            "specialty_name": presentation.specialty_name,
            "start_time": appointment.start_time.isoformat(),
        }

    def _resolve_single_offered_appointment_response(
        self,
        *,
        message: str,
        offered: Sequence[_OfferedAppointmentView],
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
        selected: _OfferedAppointmentView,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=_new_time_preference_prompt(selected.summary),
            chat_context_updates={
                **self._resolved_patient_context(chat_context),
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
                ),
                **self._selected_appointment_context(selected),
                **self._preserved_offered_appointments(chat_context),
            },
        )

    def _resolve_offered_appointment_selection(
        self,
        *,
        message: str,
        offered: Sequence[_OfferedAppointmentView],
    ) -> RescheduleAppointmentSelectionResult:
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

        specialty_query = _extract_specialty_selection_query(
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

        if _any_doctor_mentioned(normalized_message, offered):
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if _doctor_name_in_message(
                    normalized_message,
                    offered[index].doctor_name,
                )
            ]

        if _any_weekday_mentioned(normalized_message, offered):
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if offered[index].weekday.lower() in normalized_message
            ]

        normalized_time = normalize_appointment_time_expression(
            message,
            allow_bare_hour=False,
        )
        if normalized_time is not None:
            signals_detected = True
            candidate_indices = [
                index
                for index in candidate_indices
                if offered[index].time_label == normalized_time.value
            ]

        if not signals_detected:
            return RescheduleAppointmentSelectionResult(
                status=RescheduleAppointmentSelectionStatus.ZERO,
            )

        if len(candidate_indices) == 1:
            selected = offered[candidate_indices[0]]
            return RescheduleAppointmentSelectionResult(
                status=RescheduleAppointmentSelectionStatus.UNIQUE,
                selected_appointment={
                    "appointment_id": selected.appointment_id,
                    "summary": selected.summary,
                },
            )

        if not candidate_indices:
            return RescheduleAppointmentSelectionResult(
                status=RescheduleAppointmentSelectionStatus.ZERO,
            )

        return RescheduleAppointmentSelectionResult(
            status=RescheduleAppointmentSelectionStatus.AMBIGUOUS,
        )

    def _load_offered_appointment_views(
        self,
        chat_context: dict[str, Any],
    ) -> list[_OfferedAppointmentView]:
        raw_offered = chat_context.get("offered_appointments")
        if not isinstance(raw_offered, list):
            return []

        offered: list[_OfferedAppointmentView] = []
        for item in raw_offered:
            if not isinstance(item, dict):
                continue
            appointment_id = item.get("appointment_id")
            summary = item.get("summary")
            if not isinstance(appointment_id, str) or not isinstance(summary, str):
                continue
            parsed = _parse_offered_appointment_summary(summary)
            if parsed is None:
                continue
            offered.append(
                _OfferedAppointmentView(
                    appointment_id=appointment_id,
                    summary=summary,
                    doctor_id=_optional_str(item.get("doctor_id")),
                    specialty_id=_optional_str(item.get("specialty_id")),
                    start_time=_optional_str(item.get("start_time")),
                    **parsed,
                ),
            )
        return offered

    def _offered_view_from_selection(
        self,
        selected: dict[str, str],
        offered: Sequence[_OfferedAppointmentView],
    ) -> _OfferedAppointmentView | None:
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
        selected: _OfferedAppointmentView,
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


def _extract_specialty_selection_query(
    normalized_message: str,
    *,
    offered: Sequence[_OfferedAppointmentView],
) -> str | None:
    match = _SPECIALTY_SELECTION_PATTERN.search(normalized_message)
    if match is not None:
        specialty_query = match.group(1).strip().lower()
        if specialty_query not in _ORDINAL_WORDS:
            return specialty_query

    for item in offered:
        specialty = item.specialty_name.lower()
        if re.search(rf"\b{re.escape(specialty)}\b", normalized_message):
            return specialty

    return None


def _any_doctor_mentioned(
    normalized_message: str,
    offered: Sequence[_OfferedAppointmentView],
) -> bool:
    return any(
        _doctor_name_in_message(normalized_message, item.doctor_name)
        for item in offered
    )


def _any_weekday_mentioned(
    normalized_message: str,
    offered: Sequence[_OfferedAppointmentView],
) -> bool:
    return any(item.weekday.lower() in normalized_message for item in offered)


def _doctor_name_in_message(normalized_message: str, doctor_name: str) -> bool:
    normalized_name = doctor_name.replace(".", "").lower()
    if normalized_name in normalized_message:
        return True

    name_terms = [
        term
        for term in normalized_name.split()
        if term not in {"dr", "doctor"}
    ]
    if not name_terms:
        return False

    return any(term in normalized_message for term in name_terms if len(term) >= 3)


def _new_time_preference_prompt(summary: str) -> str:
    confirmation_summary = _confirmation_summary_from_list_summary(summary)
    return (
        f"Got it. What day or time would you prefer instead for your "
        f"{confirmation_summary}?"
    )
