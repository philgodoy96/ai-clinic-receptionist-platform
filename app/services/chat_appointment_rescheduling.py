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
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    _appointment_count_label,
)
from app.services.chat_booking_identity import (
    ParsedPatientFields,
    _dob_ambiguity_issue,
    _is_valid_iso_date,
    _merge_parsed_fields,
)
from app.services.chat_confirmation import normalize_patient_display_name
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.clinic_time import ClinicTimeService
from app.services.patient_identity_resolution import PatientIdentityResolutionService

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE = "reschedule"

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


@dataclass(frozen=True, slots=True)
class RescheduleFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


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
                {
                    "appointment_id": str(presentation.appointment_id),
                    "summary": presentation.list_summary,
                }
                for presentation in presentations
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
