from __future__ import annotations

import logging
import re
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
from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
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
from app.services.clinic_time import (
    ClinicTimeService,
    format_clinic_local_time_label,
    to_clinic_local_datetime,
)
from app.services.dob_ambiguity import detect_ambiguous_numeric_dob
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
_CANCELLATION_PATIENT_NOT_FOUND_MESSAGE = (
    "I couldn't find a matching patient profile with that name and date of birth. "
    "Could you check the details and try again?"
)
_CANCELLATION_NO_UPCOMING_APPOINTMENTS_MESSAGE = (
    "I'm not seeing any upcoming appointments for that patient."
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
_ORDINAL_APPOINTMENT_KEYWORDS: dict[str, int] = {
    "the first one": 0,
    "first one": 0,
    "the first": 0,
    "the second one": 1,
    "second one": 1,
    "the second": 1,
    "the third one": 2,
    "third one": 2,
    "the third": 2,
}
_ORDINAL_WORDS = frozenset({"first", "second", "third"})
_OPTION_NUMBER_PATTERN = re.compile(r"^(?:number\s+)?(\d+)$")
_SPECIALTY_SELECTION_PATTERN = re.compile(
    r"\bthe\s+([a-z][a-z\s-]*?)\s+one\b",
    re.IGNORECASE,
)


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


@dataclass(frozen=True, slots=True)
class CancellationAppointmentSelectionResult:
    status: CancellationAppointmentSelectionStatus
    selected_appointment: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class _OfferedAppointmentView:
    appointment_id: str
    summary: str
    specialty_name: str
    doctor_name: str
    weekday: str
    time_label: str


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
        base_updates = {
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
        if dob_issue is not None:
            return CancellationFlowResult(
                intent="cancel_request",
                content=dob_issue.clarification_question or (
                    "Please clarify the patient's date of birth."
                ),
                chat_context_updates=base_updates,
            )

        if parsed.full_name is None or parsed.date_of_birth is None:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
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
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates=base_updates,
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
        start_from = self.clinic_time_service.clinic_now()
        cancelable = self.appointments.list_cancelable_for_patient(
            patient_id=record.patient_id,
            start_from=start_from,
        )

        if not cancelable:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_NO_UPCOMING_APPOINTMENTS_MESSAGE,
                chat_context_updates=base_updates,
            )

        presentations = [
            self._present_appointment(appointment) for appointment in cancelable
        ]
        shared_context = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "resolved_patient_id": str(record.patient_id),
            "resolved_patient_name": resolved_name,
            "patient_resolution_id": resolution.patient_resolution_id,
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
                    {
                        "appointment_id": str(presentation.appointment_id),
                        "summary": presentation.list_summary,
                    }
                    for presentation in presentations
                ],
            },
        )

    def handle_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> CancellationFlowResult:
        base_updates = self._appointment_selection_context_updates(chat_context)

        if _message_contains_cancel_keyword(message):
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_SELECTION_REPROMPT,
                chat_context_updates=base_updates,
            )

        selection = self.resolve_appointment_selection(
            message=message,
            chat_context=chat_context,
        )

        if selection.status is CancellationAppointmentSelectionStatus.UNIQUE:
            selected = selection.selected_appointment
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

        if selection.status is CancellationAppointmentSelectionStatus.AMBIGUOUS:
            return CancellationFlowResult(
                intent="cancel_request",
                content=_CANCELLATION_APPOINTMENT_SELECTION_AMBIGUOUS,
                chat_context_updates=base_updates,
            )

        return CancellationFlowResult(
            intent="cancel_request",
            content=_CANCELLATION_APPOINTMENT_SELECTION_NO_MATCH,
            chat_context_updates=base_updates,
        )

    def resolve_appointment_selection(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> CancellationAppointmentSelectionResult:
        offered = self._load_offered_appointment_views(chat_context)
        if not offered:
            return CancellationAppointmentSelectionResult(
                status=CancellationAppointmentSelectionStatus.ZERO,
            )

        return self._resolve_offered_appointment_selection(
            message=message,
            offered=offered,
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
            "resolved_patient_id": chat_context.get("resolved_patient_id"),
            "resolved_patient_name": chat_context.get("resolved_patient_name"),
            "patient_resolution_id": chat_context.get("patient_resolution_id"),
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
            "resolved_patient_id": chat_context.get("resolved_patient_id"),
            "resolved_patient_name": chat_context.get("resolved_patient_name"),
            "patient_resolution_id": chat_context.get("patient_resolution_id"),
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
        deterministic = parse_patient_fields(message, booking_context=True)
        understanding = self._interpret_identity_turn(
            message=message,
            chat_context=chat_context,
        )
        if understanding is None or self._should_use_deterministic_only(understanding):
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

    def _resolve_offered_appointment_selection(
        self,
        *,
        message: str,
        offered: Sequence[_OfferedAppointmentView],
    ) -> CancellationAppointmentSelectionResult:
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

        ordinal_index = _extract_ordinal_index(normalized_message, option_count=len(offered))
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
            return CancellationAppointmentSelectionResult(
                status=CancellationAppointmentSelectionStatus.ZERO,
            )

        if len(candidate_indices) == 1:
            selected = offered[candidate_indices[0]]
            return CancellationAppointmentSelectionResult(
                status=CancellationAppointmentSelectionStatus.UNIQUE,
                selected_appointment={
                    "appointment_id": selected.appointment_id,
                    "summary": selected.summary,
                },
            )

        if not candidate_indices:
            return CancellationAppointmentSelectionResult(
                status=CancellationAppointmentSelectionStatus.ZERO,
            )

        return CancellationAppointmentSelectionResult(
            status=CancellationAppointmentSelectionStatus.AMBIGUOUS,
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
                    **parsed,
                ),
            )
        return offered

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


def _parse_offered_appointment_summary(
    summary: str,
) -> dict[str, str] | None:
    match = re.match(
        r"^(?P<specialty>.+?) with (?P<doctor>.+?) on (?P<weekday>\w+) at (?P<time>\d{2}:\d{2})$",
        summary,
    )
    if match is None:
        return None
    return {
        "specialty_name": match.group("specialty"),
        "doctor_name": match.group("doctor"),
        "weekday": match.group("weekday"),
        "time_label": match.group("time"),
    }


def _message_contains_cancel_keyword(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in _CANCEL_KEYWORDS)


def _extract_option_number_index(normalized_message: str, *, option_count: int) -> int | None:
    match = _OPTION_NUMBER_PATTERN.match(normalized_message.strip())
    if match is None:
        return None
    option_number = int(match.group(1))
    if option_number < 1 or option_number > option_count:
        return None
    return option_number - 1


def _extract_ordinal_index(normalized_message: str, *, option_count: int) -> int | None:
    for keyword, index in sorted(
        _ORDINAL_APPOINTMENT_KEYWORDS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if keyword in normalized_message and index < option_count:
            return index
    return None


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


def _any_weekday_mentioned(
    normalized_message: str,
    offered: Sequence[_OfferedAppointmentView],
) -> bool:
    return any(item.weekday.lower() in normalized_message for item in offered)
