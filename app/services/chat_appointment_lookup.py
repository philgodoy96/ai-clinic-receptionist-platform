from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
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
from app.services.chat_confirmation import normalize_patient_display_name
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

APPOINTMENT_MANAGEMENT_MODE_LOOKUP = "lookup"
APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY = "patient_identity"
APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED = "completed"

_LOOKUP_IDENTITY_REPROMPT_MESSAGE = (
    "I still need the patient's full name and date of birth to look up scheduled "
    "appointments."
)
_LOOKUP_IDENTITY_NAME_ONLY_MESSAGE = (
    "Thanks. What is the patient's full name?"
)
_LOOKUP_IDENTITY_DOB_ONLY_MESSAGE = (
    "Thanks. What is the patient's date of birth?"
)
_LOOKUP_IDENTITY_ENTRY_MESSAGE = (
    "I can look up your scheduled appointments. What is your full name and date of birth?"
)
_LOOKUP_PATIENT_NOT_FOUND_MESSAGE = (
    "I couldn't find a matching patient profile with that name and date of birth. "
    "Could you check the details and try again?"
)
_LOOKUP_NO_APPOINTMENTS_MESSAGE = (
    "I don't see any upcoming scheduled appointments for you. "
    "Would you like to book one?"
)
_LOOKUP_RESULTS_HEADER_SINGULAR = "Here is your upcoming scheduled appointment:"
_LOOKUP_RESULTS_HEADER_PLURAL = "Here are your upcoming scheduled appointments:"
_LOOKUP_RESULTS_FOLLOW_UP_SINGULAR = (
    "Would you like to cancel or reschedule this appointment?"
)
_LOOKUP_RESULTS_FOLLOW_UP_PLURAL = (
    "Would you like to cancel or reschedule any of these?"
)

_APPOINTMENT_LOOKUP_CANCEL_BLOCKLIST = ("cancel", "cancellation")
_APPOINTMENT_LOOKUP_RESCHEDULE_BLOCKLIST = (
    "reschedule",
    "move appointment",
    "move my appointment",
)
_APPOINTMENT_LOOKUP_VIEWING_VERBS = (
    "see",
    "show",
    "list",
    "view",
    "check",
    "look up",
    "lookup",
)
_APPOINTMENT_LOOKUP_PHRASES = (
    "show my scheduled appointments",
    "see my scheduled appointments",
    "what my scheduled appointments",
    "see what my scheduled appointments",
    "what appointments do i have",
    "what are my upcoming appointments",
    "can i see my appointments",
    "can i check my appointments",
    "do i have any appointments scheduled",
    "list my appointments",
    "view my appointments",
    "check my appointments",
    "show my appointments",
    "see my appointments",
    "look up my appointments",
    "i'd like to check my appointments",
    "i would like to check my appointments",
    "i'd like to see my appointments",
    "i would like to see my appointments",
    "my upcoming appointments",
    "my scheduled appointments",
)


def is_appointment_lookup_message(normalized_message: str) -> bool:
    """Return True when the user is asking to view their scheduled appointments."""
    if any(keyword in normalized_message for keyword in _APPOINTMENT_LOOKUP_CANCEL_BLOCKLIST):
        return False
    if any(keyword in normalized_message for keyword in _APPOINTMENT_LOOKUP_RESCHEDULE_BLOCKLIST):
        return False
    if any(phrase in normalized_message for phrase in _APPOINTMENT_LOOKUP_PHRASES):
        return True
    if "book" in normalized_message and "appointment" in normalized_message:
        return False
    if "upcoming" in normalized_message and "appointment" in normalized_message:
        return True
    if "scheduled" in normalized_message and "appointment" in normalized_message:
        if any(verb in normalized_message for verb in _APPOINTMENT_LOOKUP_VIEWING_VERBS):
            return True
    if "my appointment" in normalized_message or "my appointments" in normalized_message:
        if "availability" in normalized_message or "available" in normalized_message:
            return False
        if any(verb in normalized_message for verb in _APPOINTMENT_LOOKUP_VIEWING_VERBS):
            return True
    return False


class SchedulingMetadataForLookup(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class LookupFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    appointment_id: UUID
    list_summary: str


class ChatAppointmentLookupOrchestrator:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        scheduling_metadata: SchedulingMetadataForLookup,
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
    ) -> LookupFlowResult:
        base_updates: dict[str, Any] = {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
            ),
        }

        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            chat_context=chat_context,
            parse_patient_fields=parse_patient_fields,
        )

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
            return LookupFlowResult(
                intent="list_appointments",
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
                both_prompt=_LOOKUP_IDENTITY_REPROMPT_MESSAGE,
                name_prompt=_LOOKUP_IDENTITY_NAME_ONLY_MESSAGE,
                dob_prompt=_LOOKUP_IDENTITY_DOB_ONLY_MESSAGE,
            )
            return LookupFlowResult(
                intent="list_appointments",
                content=prompt or _LOOKUP_IDENTITY_REPROMPT_MESSAGE,
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
            return LookupFlowResult(
                intent="list_appointments",
                content=question,
                chat_context_updates={
                    **base_updates,
                    "patient_resolution_id": resolution.patient_resolution_id,
                },
            )

        if resolution.match_status is PatientResolutionMatchStatus.MULTIPLE_MATCHES:
            return LookupFlowResult(
                intent="list_appointments",
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
            return LookupFlowResult(
                intent="list_appointments",
                content=_LOOKUP_PATIENT_NOT_FOUND_MESSAGE,
                chat_context_updates={
                    **base_updates,
                    APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
                },
            )

        record = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=resolution.patient_resolution_id or "",
            conversation_id=conversation.id,
        )
        if record is None:
            return LookupFlowResult(
                intent="list_appointments",
                content=_LOOKUP_PATIENT_NOT_FOUND_MESSAGE,
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
        resolved_updates[APPOINTMENT_MANAGEMENT_IDENTITY_KEY] = None

        start_from = self.clinic_time_service.clinic_now()
        upcoming = self.appointments.list_upcoming_for_patient(
            patient_id=record.patient_id,
            start_from=start_from,
        )

        return self._build_listing_result(
            resolved_context_updates=resolved_updates,
            upcoming=upcoming,
        )

    def list_appointments_for_resolved_patient(
        self,
        *,
        chat_context: dict[str, Any],
    ) -> LookupFlowResult | None:
        """Reuse an already-resolved patient to list scheduled appointments.

        Returns ``None`` when no resolved patient identity is available.
        """
        resolved = read_resolved_patient_context(chat_context)
        if resolved is None:
            return None
        try:
            patient_id = UUID(resolved.patient_id)
        except ValueError:
            return None

        start_from = self.clinic_time_service.clinic_now()
        upcoming = self.appointments.list_upcoming_for_patient(
            patient_id=patient_id,
            start_from=start_from,
        )
        return self._build_listing_result(
            resolved_context_updates=self._resolved_patient_context_updates(resolved),
            upcoming=upcoming,
        )

    def _build_listing_result(
        self,
        *,
        resolved_context_updates: dict[str, Any],
        upcoming: Sequence[Appointment],
    ) -> LookupFlowResult:
        shared_context = {
            **resolved_context_updates,
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
            "lookup_status": "listed",
            # Default: no empty-state follow-up. Set only when nothing is found.
            APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY: None,
        }

        if not upcoming:
            # Patient resolved but has no scheduled appointments. The message
            # offers to book one, so the next "yes" must route to scheduling.
            return LookupFlowResult(
                intent="list_appointments",
                content=_LOOKUP_NO_APPOINTMENTS_MESSAGE,
                chat_context_updates={
                    **shared_context,
                    "offered_appointments": None,
                    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY: (
                        APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING
                    ),
                },
            )

        presentations = [
            self._present_appointment(appointment) for appointment in upcoming
        ]
        options = "\n".join(
            f"{index}. {presentation.list_summary}."
            for index, presentation in enumerate(presentations, start=1)
        )
        if len(presentations) == 1:
            header = _LOOKUP_RESULTS_HEADER_SINGULAR
            follow_up = _LOOKUP_RESULTS_FOLLOW_UP_SINGULAR
        else:
            header = _LOOKUP_RESULTS_HEADER_PLURAL
            follow_up = _LOOKUP_RESULTS_FOLLOW_UP_PLURAL
        content = (
            f"{header}\n\n"
            f"{options}\n\n"
            f"{follow_up}"
        )

        return LookupFlowResult(
            intent="list_appointments",
            content=content,
            chat_context_updates={
                **shared_context,
                "offered_appointments": [
                    {
                        "appointment_id": str(presentation.appointment_id),
                        "summary": presentation.list_summary,
                    }
                    for presentation in presentations
                ],
            },
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

    def _present_appointment(self, appointment: Appointment) -> _AppointmentPresentation:
        doctor_name = self._resolve_doctor_name(appointment.doctor_id)
        specialty_name = self._resolve_specialty_name(appointment.specialty_id)
        clinic_tz = self.clinic_time_service.timezone
        localized_start = to_clinic_local_datetime(appointment.start_time, clinic_tz)
        date_part = (
            f"{localized_start.strftime('%A')}, "
            f"{localized_start.strftime('%B')} {localized_start.day}"
        )
        time_label = format_clinic_local_time_label(appointment.start_time, clinic_tz)
        list_summary = (
            f"{specialty_name} with {doctor_name} on {date_part} at {time_label}"
        )

        return _AppointmentPresentation(
            appointment_id=appointment.id,
            list_summary=list_summary,
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
            conversation_state=ConversationState.APPOINTMENT_LOOKUP_INTAKE,
            expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
            latest_user_message=message,
            allowed_intents=[ChatTurnIntent.PATIENT_IDENTITY_PROVIDED],
            current_context={
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
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
                "chat turn understanding interpreter failed during lookup identity intake",
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
