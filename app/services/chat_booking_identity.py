from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConversationState,
    ExpectedResponseType,
    FieldIssue,
    PatientStatusAnswer,
)
from app.domain.patient_identity_resolution import (
    PatientIdentityResolutionRequest,
    PatientResolutionMatchStatus,
    PatientResolutionNextStep,
)
from app.models.conversations import Conversation
from app.services.chat_confirmation import (
    ConfirmationDecision,
    ConfirmationType,
    is_confirmation_confirmed,
    is_confirmation_rejected,
    normalize_email_address,
    normalize_patient_display_name,
    understand_confirmation,
)
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.clinic_time import format_clinic_local_time_label
from app.services.dob_ambiguity import (
    detect_ambiguous_numeric_dob,
    merge_dob_ambiguity_context_updates,
    try_resolve_pending_dob_ambiguity,
)
from app.services.patient_identity_resolution import (
    PatientIdentityResolutionService,
    PatientResolutionNotFoundError,
)

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

_SEEN_BEFORE_YES_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "i have",
    "i've been",
    "been seen",
    "returning",
    "existing",
)
_SEEN_BEFORE_NO_PHRASES = (
    "no",
    "nope",
    "haven't",
    "have not",
    "not been",
    "first time",
    "new patient",
    "never been",
    "i'm new",
    "im new",
)


class ChatBookingIdentityStep(StrEnum):
    ASK_SEEN_BEFORE = "ask_seen_before"
    COLLECT_EXISTING_IDENTITY = "collect_existing_identity"
    COLLECT_NEW_NAME = "collect_new_name"
    COLLECT_NEW_DOB = "collect_new_dob"
    COLLECT_NEW_EMAIL = "collect_new_email"
    CONFIRM_NEW_EMAIL = "confirm_new_email"
    COLLECT_CONFIRMATION_EMAIL = "collect_confirmation_email"
    CONFIRM_CONFIRMATION_EMAIL = "confirm_confirmation_email"
    CONFIRM_POSSIBLE_MATCH = "confirm_possible_match"
    AWAIT_FINAL_BOOKING_CONFIRMATION = "await_final_booking_confirmation"
    BOOKING_COMPLETED = "booking_completed"


_IDENTITY_TURN_UNDERSTANDING_STEPS = frozenset(
    {
        ChatBookingIdentityStep.ASK_SEEN_BEFORE,
        ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY,
        ChatBookingIdentityStep.COLLECT_NEW_NAME,
        ChatBookingIdentityStep.COLLECT_NEW_DOB,
        ChatBookingIdentityStep.COLLECT_NEW_EMAIL,
        ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL,
    },
)


@dataclass(frozen=True, slots=True)
class ParsedPatientFields:
    full_name: str | None = None
    date_of_birth: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class BookingIdentityFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any] = field(default_factory=dict)
    hold_id: str | None = None
    booking_attempted: bool = False


@dataclass(frozen=True, slots=True)
class _SummaryFacts:
    appointment_label: str
    doctor_name: str
    appointment_date: str
    appointment_time: str
    patient_name: str
    confirmed_email: str


# Conversation-level resolved patient memory. Once written chat resolves (or
# creates) a patient, these keys let booking/cancellation/reschedule flows reuse
# that identity without asking for name + date of birth again. The backend still
# validates ownership before any cancel/reschedule executes; this is UX state.
RESOLVED_PATIENT_ID_KEY = "resolved_patient_id"
RESOLVED_PATIENT_NAME_KEY = "resolved_patient_name"
RESOLVED_PATIENT_DOB_KEY = "resolved_patient_date_of_birth"
RESOLVED_PATIENT_EMAIL_KEY = "resolved_patient_email"
PATIENT_RESOLUTION_ID_KEY = "patient_resolution_id"

RESOLVED_PATIENT_CONTEXT_KEYS = (
    RESOLVED_PATIENT_ID_KEY,
    RESOLVED_PATIENT_NAME_KEY,
    RESOLVED_PATIENT_DOB_KEY,
    RESOLVED_PATIENT_EMAIL_KEY,
    PATIENT_RESOLUTION_ID_KEY,
)

# Partial identity collected during a cancellation/reschedule identity intake.
# Keeps already-known fields (e.g. the name) so we only re-ask for the missing
# field and so an ambiguous-DOB clarification can resume without re-asking name.
APPOINTMENT_MANAGEMENT_IDENTITY_KEY = "appointment_management_identity"


@dataclass(frozen=True, slots=True)
class ResolvedPatientContext:
    patient_id: str
    name: str | None = None
    date_of_birth: str | None = None
    email: str | None = None
    patient_resolution_id: str | None = None


def read_resolved_patient_context(
    chat_context: dict[str, Any],
) -> ResolvedPatientContext | None:
    """Read a previously resolved patient identity from chat context, if any."""
    patient_id = chat_context.get(RESOLVED_PATIENT_ID_KEY)
    if not isinstance(patient_id, str) or not patient_id.strip():
        return None

    def _clean(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    return ResolvedPatientContext(
        patient_id=patient_id,
        name=_clean(chat_context.get(RESOLVED_PATIENT_NAME_KEY)),
        date_of_birth=_clean(chat_context.get(RESOLVED_PATIENT_DOB_KEY)),
        email=_clean(chat_context.get(RESOLVED_PATIENT_EMAIL_KEY)),
        patient_resolution_id=_clean(chat_context.get(PATIENT_RESOLUTION_ID_KEY)),
    )


def build_resolved_patient_context_updates(
    *,
    patient_id: str,
    name: str | None = None,
    date_of_birth: str | None = None,
    email: str | None = None,
    patient_resolution_id: str | None = None,
) -> dict[str, Any]:
    """Build chat-context updates that persist a resolved patient identity."""
    updates: dict[str, Any] = {RESOLVED_PATIENT_ID_KEY: patient_id}
    if name:
        updates[RESOLVED_PATIENT_NAME_KEY] = name
    if date_of_birth:
        updates[RESOLVED_PATIENT_DOB_KEY] = date_of_birth
    if email:
        updates[RESOLVED_PATIENT_EMAIL_KEY] = email
    if patient_resolution_id:
        updates[PATIENT_RESOLUTION_ID_KEY] = patient_resolution_id
    return updates


def merge_appointment_management_identity(
    existing: Any,
    parsed: ParsedPatientFields,
    *,
    include_date_of_birth: bool,
) -> dict[str, Any]:
    """Merge newly parsed identity fields into the partial identity store.

    Existing values win so we never clobber a field the patient already gave.
    The date of birth is only merged when ``include_date_of_birth`` is True so an
    ambiguous numeric DOB does not get stored before it is disambiguated.
    """
    merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    if parsed.full_name and not merged.get("full_name"):
        normalized = normalize_patient_display_name(parsed.full_name)
        if normalized:
            merged["full_name"] = normalized
    if include_date_of_birth and parsed.date_of_birth and not merged.get("date_of_birth"):
        merged["date_of_birth"] = parsed.date_of_birth
    if parsed.email and not merged.get("email"):
        normalized_email = normalize_email_address(parsed.email)
        if normalized_email:
            merged["email"] = normalized_email
    if parsed.phone and not merged.get("phone"):
        merged["phone"] = parsed.phone
    return merged


def appointment_management_missing_identity_prompt(
    identity: dict[str, Any],
    *,
    both_prompt: str,
    name_prompt: str,
    dob_prompt: str,
) -> str | None:
    """Pick the right re-prompt based on which identity fields are still missing.

    Returns ``None`` when both name and date of birth are present.
    """
    has_name = bool(identity.get("full_name"))
    has_dob = bool(identity.get("date_of_birth"))
    if has_name and has_dob:
        return None
    if has_name and not has_dob:
        return dob_prompt
    if has_dob and not has_name:
        return name_prompt
    return both_prompt


class ChatBookingIdentityOrchestrator:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
        clinic_timezone: ZoneInfo | None = None,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.chat_turn_understanding_interpreter = chat_turn_understanding_interpreter
        self._clinic_timezone = clinic_timezone

    def hold_created_context_updates(self, base_updates: dict[str, Any]) -> dict[str, Any]:
        return {
            **base_updates,
            "booking_identity_step": ChatBookingIdentityStep.ASK_SEEN_BEFORE.value,
        }

    def hold_created_message(self, *, display_time: str, doctor_name: str) -> str:
        return (
            f"I can hold {display_time} with {doctor_name} while we confirm the details. "
            "Have you been seen here before?"
        )

    def handle(
        self,
        *,
        message: str,
        conversation: Conversation,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        parse_patient_fields: Any,
        format_missing_identity_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult | None:
        del format_missing_identity_fields
        step = self._current_step(merged_context)
        if step is None:
            return None

        if merged_context.get("appointment_id"):
            return self._already_booked_reply(
                merged_context=merged_context,
                context_updates=context_updates,
                hold_id=hold_id,
            )

        updates = dict(context_updates)
        booking_context = {**merged_context, **updates}

        if step is ChatBookingIdentityStep.BOOKING_COMPLETED:
            return self._already_booked_reply(
                merged_context=merged_context,
                context_updates=updates,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.ASK_SEEN_BEFORE:
            return self._handle_ask_seen_before(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY:
            return self._handle_collect_existing_identity(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.COLLECT_NEW_NAME:
            return self._handle_collect_new_name(
                message=message,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.COLLECT_NEW_DOB:
            return self._handle_collect_new_dob(
                message=message,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.COLLECT_NEW_EMAIL:
            return self._handle_collect_new_email(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.CONFIRM_NEW_EMAIL:
            return self._handle_confirm_new_email(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL:
            return self._handle_collect_confirmation_email(
                message=message,
                booking_context=booking_context,
                updates=updates,
                parse_patient_fields=parse_patient_fields,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.CONFIRM_CONFIRMATION_EMAIL:
            return self._handle_confirm_confirmation_email(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.CONFIRM_POSSIBLE_MATCH:
            return self._handle_confirm_possible_match(
                message=message,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                hold_id=hold_id,
            )

        if step is ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION:
            if is_confirmation_confirmed(
                confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
                message=message,
            ):
                return None
            return BookingIdentityFlowResult(
                intent="booking_confirmation_required",
                content=(
                    "Please confirm if you would like me to book that appointment, "
                    "or let me know if you need to change anything."
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        return None

    def should_attempt_booking(
        self,
        *,
        merged_context: dict[str, Any],
        message: str,
    ) -> bool:
        if merged_context.get("appointment_id"):
            return False

        step = self._current_step(merged_context)
        if step is ChatBookingIdentityStep.BOOKING_COMPLETED:
            return False

        if step is not ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION:
            return False

        return is_confirmation_confirmed(
            confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
            message=message,
        )

    def is_active(self, merged_context: dict[str, Any]) -> bool:
        step = self._current_step(merged_context)
        if step is None:
            return False
        return step is not ChatBookingIdentityStep.BOOKING_COMPLETED

    def booking_completed_context_updates(self) -> dict[str, Any]:
        return {
            "booking_identity_step": ChatBookingIdentityStep.BOOKING_COMPLETED.value,
        }

    def _current_step(self, merged_context: dict[str, Any]) -> ChatBookingIdentityStep | None:
        raw_step = merged_context.get("booking_identity_step")
        if not isinstance(raw_step, str):
            return None

        try:
            return ChatBookingIdentityStep(raw_step)
        except ValueError:
            return None

    def _uses_turn_understanding(self, step: ChatBookingIdentityStep) -> bool:
        return (
            self.chat_turn_understanding_interpreter is not None
            and step in _IDENTITY_TURN_UNDERSTANDING_STEPS
        )

    def _build_turn_understanding_request(
        self,
        *,
        message: str,
        step: ChatBookingIdentityStep,
        booking_context: dict[str, Any],
        hold_id: str,
    ) -> ChatTurnUnderstandingRequest:
        if step is ChatBookingIdentityStep.ASK_SEEN_BEFORE:
            conversation_state = ConversationState.COLLECTING_PATIENT_STATUS
            expected_response_type = ExpectedResponseType.PATIENT_STATUS
            allowed_intents = [
                ChatTurnIntent.PATIENT_STATUS_ANSWER,
                ChatTurnIntent.PATIENT_IDENTITY_PROVIDED,
                ChatTurnIntent.FALLBACK,
            ]
        else:
            conversation_state = ConversationState.COLLECTING_PATIENT_IDENTITY
            expected_response_type = ExpectedResponseType.PATIENT_IDENTITY
            allowed_intents = [
                ChatTurnIntent.PATIENT_IDENTITY_PROVIDED,
                ChatTurnIntent.FALLBACK,
            ]

        current_context: dict[str, Any] = {
            "booking_identity_step": step.value,
            "hold_active": bool(hold_id),
        }
        patient_identity = booking_context.get("patient_identity")
        if isinstance(patient_identity, dict) and patient_identity:
            current_context["patient_identity"] = patient_identity
        if "patient_seen_before" in booking_context:
            current_context["patient_seen_before"] = booking_context["patient_seen_before"]

        return ChatTurnUnderstandingRequest(
            conversation_state=conversation_state,
            expected_response_type=expected_response_type,
            latest_user_message=message,
            allowed_intents=allowed_intents,
            current_context=current_context,
        )

    def _interpret_identity_turn(
        self,
        *,
        message: str,
        step: ChatBookingIdentityStep,
        booking_context: dict[str, Any],
        hold_id: str,
    ) -> ChatTurnUnderstandingResult | None:
        if not self._uses_turn_understanding(step):
            return None

        request = self._build_turn_understanding_request(
            message=message,
            step=step,
            booking_context=booking_context,
            hold_id=hold_id,
        )
        try:
            return self.chat_turn_understanding_interpreter.interpret(request)  # type: ignore[union-attr]
        except Exception:
            logger.exception("chat turn understanding interpreter failed during identity intake")
            return None

    def _should_use_deterministic_only(
        self,
        understanding: ChatTurnUnderstandingResult,
    ) -> bool:
        if understanding.intent is ChatTurnIntent.FALLBACK:
            return True
        return understanding.confidence < _CTU_LOW_CONFIDENCE_THRESHOLD

    def _resolve_patient_fields(
        self,
        *,
        message: str,
        step: ChatBookingIdentityStep,
        booking_context: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
        understanding: ChatTurnUnderstandingResult | None = None,
    ) -> tuple[ParsedPatientFields, FieldIssue | None]:
        confirmed_iso, rejection_issue = try_resolve_pending_dob_ambiguity(
            message,
            booking_context,
        )
        if rejection_issue is not None:
            return ParsedPatientFields(), rejection_issue

        deterministic = parse_patient_fields(message, booking_context=True)
        if understanding is None:
            understanding = self._interpret_identity_turn(
                message=message,
                step=step,
                booking_context=booking_context,
                hold_id=hold_id,
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
            normalized_email = normalize_email_address(extracted.email)
            if normalized_email:
                email = normalized_email

        return (
            ParsedPatientFields(
                full_name=full_name,
                date_of_birth=date_of_birth,
                email=email,
                phone=None,
            ),
            dob_issue,
        )

    def _already_booked_reply(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        doctor_name = merged_context.get("selected_doctor_name")
        appointment_date = _format_summary_date(merged_context)
        appointment_time = _format_summary_time(
            merged_context,
            clinic_timezone=self._clinic_timezone,
        )
        details = ""
        if (
            isinstance(doctor_name, str)
            and doctor_name.strip()
            and appointment_date is not None
            and appointment_time is not None
        ):
            details = (
                f" Your appointment with {doctor_name.strip()} on {appointment_date} "
                f"at {appointment_time} is already confirmed."
            )
        return BookingIdentityFlowResult(
            intent="booking_confirmed",
            content=(
                "Your appointment is already confirmed."
                f"{details} Is there anything else I can help with?"
            ),
            chat_context_updates=context_updates,
            hold_id=hold_id,
        )

    def _handle_ask_seen_before(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        seen_before = _parse_seen_before_answer(message)
        understanding = self._interpret_identity_turn(
            message=message,
            step=ChatBookingIdentityStep.ASK_SEEN_BEFORE,
            booking_context=booking_context,
            hold_id=hold_id,
        )
        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.ASK_SEEN_BEFORE,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
            understanding=understanding,
        )

        if understanding is not None and not self._should_use_deterministic_only(understanding):
            if understanding.patient_status_answer is PatientStatusAnswer.EXISTING_PATIENT:
                seen_before = True
            elif understanding.patient_status_answer is PatientStatusAnswer.NEW_PATIENT:
                seen_before = False

        if seen_before is True:
            updates["patient_seen_before"] = True
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value
            )
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=(
                    "Great. What name and date of birth should I use to look up your profile?"
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if seen_before is False:
            return self._advance_new_patient_from_parsed_fields(
                parsed=parsed,
                dob_issue=dob_issue,
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                hold_id=hold_id,
            )

        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="Have you been seen at this clinic before? Please answer yes or no.",
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _advance_new_patient_from_parsed_fields(
        self,
        *,
        parsed: ParsedPatientFields,
        dob_issue: FieldIssue | None,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        updates["patient_seen_before"] = False
        identity = dict(booking_context.get("patient_identity") or {})

        if parsed.full_name:
            identity["full_name"] = parsed.full_name
        if parsed.date_of_birth:
            identity["date_of_birth"] = parsed.date_of_birth

        # Written chat: the patient typed their email, so treat a valid address
        # as confirmed immediately instead of asking "I heard ... is that
        # correct?". Voice keeps explicit confirmation because transcription can
        # mishear an address; that path lives outside this orchestrator.
        email = normalize_email_address(parsed.email) if parsed.email else None
        if email:
            identity["email"] = email
            updates["confirmed_booking_email"] = email

        if identity:
            updates["patient_identity"] = identity

        if dob_issue is not None:
            updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_DOB.value
            merge_dob_ambiguity_context_updates(
                updates,
                dob_issue=dob_issue,
                date_of_birth=None,
            )
            clarification = dob_issue.clarification_question or (
                "Please clarify your date of birth."
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=clarification,
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        has_name = isinstance(identity.get("full_name"), str)
        has_dob = isinstance(identity.get("date_of_birth"), str)
        merge_dob_ambiguity_context_updates(
            updates,
            dob_issue=None,
            date_of_birth=identity.get("date_of_birth")
            if isinstance(identity.get("date_of_birth"), str)
            else None,
        )

        if has_name and has_dob and email:
            return self._resolve_new_patient_with_email(
                conversation=conversation,
                booking_context=booking_context,
                updates=updates,
                hold_id=hold_id,
            )

        if has_name and has_dob:
            updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_EMAIL.value
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content="What email should we use for the confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if has_name:
            updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_DOB.value
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content="What is your date of birth?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_NAME.value
        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="No problem. What name should I put on the appointment?",
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _resolve_new_patient_with_email(
        self,
        *,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        identity = {
            **(booking_context.get("patient_identity") or {}),
            **(updates.get("patient_identity") or {}),
        }
        full_name = identity.get("full_name")
        date_of_birth = identity.get("date_of_birth")
        email = updates.get("confirmed_booking_email") or identity.get("email")
        if (
            not isinstance(full_name, str)
            or not isinstance(date_of_birth, str)
            or not isinstance(email, str)
        ):
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="I still need your name and date of birth before we continue.",
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        result = self.patient_identity_resolution.resolve(
            PatientIdentityResolutionRequest(
                patient_name=full_name,
                patient_date_of_birth=date.fromisoformat(date_of_birth),
                conversation_id=conversation.id,
                patient_email=email,
                patient_phone=identity.get("phone"),
                caller_claims_existing_patient=False,
                allow_demo_patient_creation=True,
            ),
        )

        return self._handle_resolution_result(
            result=result,
            booking_context={**booking_context, **updates},
            updates=updates,
            hold_id=hold_id,
            patient_display_name=full_name,
            confirmed_email=email,
        )

    def _handle_collect_existing_identity(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
        )

        # Preserve any identity fields collected so far so the patient never has
        # to repeat a value. The DOB is only merged when it is unambiguous.
        identity = merge_appointment_management_identity(
            booking_context.get("patient_identity"),
            parsed,
            include_date_of_birth=dob_issue is None,
        )
        if identity:
            updates["patient_identity"] = identity

        if dob_issue is not None:
            merge_dob_ambiguity_context_updates(
                updates,
                dob_issue=dob_issue,
                date_of_birth=None,
            )
            clarification = dob_issue.clarification_question or (
                "Please clarify your date of birth."
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=clarification,
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        full_name = identity.get("full_name")
        date_of_birth = identity.get("date_of_birth")
        merge_dob_ambiguity_context_updates(
            updates,
            dob_issue=None,
            date_of_birth=date_of_birth if isinstance(date_of_birth, str) else None,
        )
        if not isinstance(full_name, str) or not isinstance(date_of_birth, str):
            prompt = appointment_management_missing_identity_prompt(
                identity,
                both_prompt=(
                    "I still need your full name and date of birth to look up your profile. "
                    "Your hold is still active."
                ),
                name_prompt=(
                    "Thanks. I still need your full name to look up your profile. "
                    "Your hold is still active."
                ),
                dob_prompt=(
                    "Thanks. I still need your date of birth to look up your profile. "
                    "Your hold is still active."
                ),
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=prompt or "I still need your full name and date of birth.",
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        full_name = normalize_patient_display_name(full_name)
        identity = {**identity, "full_name": full_name}
        updates["patient_identity"] = identity

        result = self.patient_identity_resolution.resolve(
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

        return self._handle_resolution_result(
            result=result,
            booking_context=booking_context,
            updates=updates,
            hold_id=hold_id,
            patient_display_name=full_name,
        )

    def _handle_collect_new_name(
        self,
        *,
        message: str,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.COLLECT_NEW_NAME,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
        )
        if dob_issue is not None:
            merge_dob_ambiguity_context_updates(
                updates,
                dob_issue=dob_issue,
                date_of_birth=None,
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=dob_issue.clarification_question or "Please clarify your date of birth.",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        raw_name = parsed.full_name or message.strip()
        name = normalize_patient_display_name(raw_name)
        if not name or _looks_like_scheduling_text(name):
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What name should I put on the appointment?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        updates["patient_identity"] = {
            **(booking_context.get("patient_identity") or {}),
            "full_name": name,
        }
        updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_DOB.value
        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="What is your date of birth?",
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _handle_collect_new_dob(
        self,
        *,
        message: str,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed, dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.COLLECT_NEW_DOB,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
        )
        if dob_issue is not None:
            merge_dob_ambiguity_context_updates(
                updates,
                dob_issue=dob_issue,
                date_of_birth=None,
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=dob_issue.clarification_question or "Please clarify your date of birth.",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if parsed.date_of_birth is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="Please provide your date of birth in YYYY-MM-DD format.",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        updates["patient_identity"] = {
            **(booking_context.get("patient_identity") or {}),
            "date_of_birth": parsed.date_of_birth,
        }
        merge_dob_ambiguity_context_updates(
            updates,
            dob_issue=None,
            date_of_birth=parsed.date_of_birth,
        )
        updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_EMAIL.value
        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="What email should we use for the confirmation?",
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _handle_collect_new_email(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed, _dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.COLLECT_NEW_EMAIL,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
        )
        if parsed.email is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        # Written chat: accept the typed email without a separate confirmation
        # turn. The final booking summary still echoes the address so the user
        # can catch any mistake before the appointment is booked.
        email = normalize_email_address(parsed.email)
        updates["confirmed_booking_email"] = email
        updates["patient_identity"] = {
            **(booking_context.get("patient_identity") or {}),
            "email": email,
        }
        updates.pop("pending_confirmation_email", None)
        return self._resolve_new_patient_with_email(
            conversation=conversation,
            booking_context=booking_context,
            updates=updates,
            hold_id=hold_id,
        )

    def _handle_confirm_new_email(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        pending_email = booking_context.get("pending_confirmation_email")
        if not isinstance(pending_email, str):
            updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_EMAIL.value
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        understanding = understand_confirmation(
            confirmation_type=ConfirmationType.EMAIL_CONFIRMATION,
            message=message,
        )
        if understanding.decision is not ConfirmationDecision.CONFIRMED:
            if understanding.decision is ConfirmationDecision.REJECTED or (
                understanding.decision is ConfirmationDecision.WANTS_CHANGE
            ):
                updates.pop("pending_confirmation_email", None)
                updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_EMAIL.value
                return BookingIdentityFlowResult(
                    intent="patient_identity_partial",
                    content="No problem. What email should we use for the confirmation?",
                    chat_context_updates=updates,
                    hold_id=hold_id,
                )

            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=f"I heard {pending_email} — is that correct?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        identity = booking_context.get("patient_identity") or {}
        full_name = identity.get("full_name")
        date_of_birth = identity.get("date_of_birth")
        if not isinstance(full_name, str) or not isinstance(date_of_birth, str):
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="I still need your name and date of birth before we continue.",
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        updates["confirmed_booking_email"] = pending_email
        updates["patient_identity"] = {**identity, "email": pending_email}
        updates.pop("pending_confirmation_email", None)

        result = self.patient_identity_resolution.resolve(
            PatientIdentityResolutionRequest(
                patient_name=full_name,
                patient_date_of_birth=date.fromisoformat(date_of_birth),
                conversation_id=conversation.id,
                patient_email=pending_email,
                patient_phone=identity.get("phone"),
                caller_claims_existing_patient=False,
                allow_demo_patient_creation=True,
            ),
        )

        return self._handle_resolution_result(
            result=result,
            booking_context={**booking_context, **updates},
            updates=updates,
            hold_id=hold_id,
            patient_display_name=full_name,
            confirmed_email=pending_email,
        )

    def _handle_collect_confirmation_email(
        self,
        *,
        message: str,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed, _dob_issue = self._resolve_patient_fields(
            message=message,
            step=ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL,
            booking_context=booking_context,
            parse_patient_fields=parse_patient_fields,
            hold_id=hold_id,
        )
        if parsed.email is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the appointment confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        # Written chat: accept the typed email immediately and move to the final
        # booking summary, which still shows the address for a last check.
        email = normalize_email_address(parsed.email)
        updates["confirmed_booking_email"] = email
        updates["patient_identity"] = {
            **(booking_context.get("patient_identity") or {}),
            "email": email,
        }
        updates.pop("pending_confirmation_email", None)
        return self._build_final_summary_reply(
            booking_context={**booking_context, **updates},
            updates=updates,
            hold_id=hold_id,
        )

    def _handle_confirm_confirmation_email(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        del conversation
        pending_email = booking_context.get("pending_confirmation_email")
        if not isinstance(pending_email, str):
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL.value
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the appointment confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        understanding = understand_confirmation(
            confirmation_type=ConfirmationType.EMAIL_CONFIRMATION,
            message=message,
        )
        if understanding.decision is not ConfirmationDecision.CONFIRMED:
            if understanding.decision is ConfirmationDecision.REJECTED or (
                understanding.decision is ConfirmationDecision.WANTS_CHANGE
            ):
                updates.pop("pending_confirmation_email", None)
                updates["booking_identity_step"] = (
                    ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL.value
                )
                return BookingIdentityFlowResult(
                    intent="patient_identity_partial",
                    content="No problem. What email should we use for the confirmation?",
                    chat_context_updates=updates,
                    hold_id=hold_id,
                )

            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=f"I heard {pending_email} — is that correct?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        updates["confirmed_booking_email"] = pending_email
        identity = booking_context.get("patient_identity") or {}
        updates["patient_identity"] = {**identity, "email": pending_email}
        updates.pop("pending_confirmation_email", None)

        return self._build_final_summary_reply(
            booking_context={**booking_context, **updates},
            updates=updates,
            hold_id=hold_id,
        )

    def _handle_confirm_possible_match(
        self,
        *,
        message: str,
        conversation: Conversation,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        resolution_id = booking_context.get("patient_resolution_id")
        if not isinstance(resolution_id, str):
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=(
                    "Let's start again with your name and date of birth so I can look up "
                    "your profile."
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if is_confirmation_confirmed(
            confirmation_type=ConfirmationType.POSSIBLE_PATIENT_MATCH_CONFIRMATION,
            message=message,
        ):
            try:
                result = self.patient_identity_resolution.confirm_resolution(
                    patient_resolution_id=resolution_id,
                    conversation_id=conversation.id,
                )
            except PatientResolutionNotFoundError:
                return self._identity_not_resolved_reply(updates, hold_id=hold_id)

            display_name = result.display_name
            if isinstance(display_name, str):
                display_name = normalize_patient_display_name(display_name)
            updates["patient_resolution_id"] = result.patient_resolution_id
            updates["patient_display_name"] = display_name
            if booking_context.get("confirmed_booking_email"):
                return self._build_final_summary_reply(
                    booking_context={**booking_context, **updates},
                    updates=updates,
                    hold_id=hold_id,
                )

            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL.value
            )
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content="Thanks for confirming. What email should we use for the confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if is_confirmation_rejected(
            confirmation_type=ConfirmationType.POSSIBLE_PATIENT_MATCH_CONFIRMATION,
            message=message,
        ):
            self.patient_identity_resolution.reject_resolution(
                patient_resolution_id=resolution_id,
                conversation_id=conversation.id,
            )
            updates.pop("patient_resolution_id", None)
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value
            )
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=(
                    "Okay. Could you provide the email or phone number on file, "
                    "or share another name and date of birth?"
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        question = booking_context.get("possible_match_question")
        if isinstance(question, str) and question.strip():
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=question,
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="Please let me know if that profile is yours.",
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _handle_resolution_result(
        self,
        *,
        result: Any,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
        patient_display_name: str,
        confirmed_email: str | None = None,
    ) -> BookingIdentityFlowResult:
        merged_context = {**booking_context, **updates}
        normalized_display_name = normalize_patient_display_name(patient_display_name)

        if result.match_status is PatientResolutionMatchStatus.EXACT_MATCH:
            updates["patient_resolution_id"] = result.patient_resolution_id
            resolved_name = result.display_name or normalized_display_name
            if isinstance(resolved_name, str):
                resolved_name = normalize_patient_display_name(resolved_name)
            updates["patient_display_name"] = resolved_name
            if confirmed_email:
                updates["confirmed_booking_email"] = confirmed_email
                updates["patient_identity"] = {
                    **(updates.get("patient_identity") or {}),
                    "email": confirmed_email,
                }
                prefix = "I found a matching profile. "
                summary = self._build_final_summary_reply(
                    booking_context={**merged_context, **updates},
                    updates=updates,
                    hold_id=hold_id,
                )
                return BookingIdentityFlowResult(
                    intent=summary.intent,
                    content=f"{prefix}{summary.content}",
                    chat_context_updates=summary.chat_context_updates,
                    hold_id=hold_id,
                )

            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL.value
            )
            return BookingIdentityFlowResult(
                intent="patient_identity_complete",
                content=(
                    "I found your profile. What email should we use for the confirmation?"
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if result.match_status is PatientResolutionMatchStatus.CREATED:
            updates["patient_resolution_id"] = result.patient_resolution_id
            resolved_name = result.display_name or normalized_display_name
            if isinstance(resolved_name, str):
                resolved_name = normalize_patient_display_name(resolved_name)
            updates["patient_display_name"] = resolved_name
            return self._build_final_summary_reply(
                booking_context={**merged_context, **updates},
                updates=updates,
                hold_id=hold_id,
            )

        if result.match_status is PatientResolutionMatchStatus.POSSIBLE_MATCH:
            updates["patient_resolution_id"] = result.patient_resolution_id
            updates["possible_match_question"] = result.confirmation_question
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.CONFIRM_POSSIBLE_MATCH.value
            )
            question = result.confirmation_question or result.suggested_response_text
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content=question,
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        if result.match_status is PatientResolutionMatchStatus.MULTIPLE_MATCHES:
            return BookingIdentityFlowResult(
                intent="booking_conflict",
                content=(
                    result.suggested_response_text
                    or (
                        "I found more than one possible profile. "
                        "Could you provide the email or phone number on file?"
                    )
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        if result.next_step is PatientResolutionNextStep.ASK_EMAIL_OR_PHONE:
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=result.suggested_response_text,
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        return self._identity_not_resolved_reply(updates, hold_id=hold_id)

    def _build_final_summary_reply(
        self,
        *,
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        resolution_id = updates.get("patient_resolution_id") or booking_context.get(
            "patient_resolution_id",
        )
        if not isinstance(resolution_id, str):
            return self._identity_not_resolved_reply(updates, hold_id=hold_id)

        confirmed_email = updates.get("confirmed_booking_email") or booking_context.get(
            "confirmed_booking_email",
        )
        if not isinstance(confirmed_email, str):
            updates["booking_identity_step"] = (
                ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL.value
            )
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the appointment confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        summary_facts = _collect_summary_facts(
            booking_context=booking_context,
            confirmed_email=confirmed_email,
            clinic_timezone=self._clinic_timezone,
        )
        if summary_facts is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=(
                    "I still need to confirm the appointment details before booking. "
                    "Your hold is still active."
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        updates["booking_identity_step"] = (
            ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION.value
        )
        updates["patient_identity_resolved"] = True
        updates["patient_display_name"] = summary_facts.patient_name

        summary = (
            f"Before I book it, please confirm: {summary_facts.appointment_label} with "
            f"{summary_facts.doctor_name} on {summary_facts.appointment_date} at "
            f"{summary_facts.appointment_time} for {summary_facts.patient_name}, using "
            f"the confirmed email {summary_facts.confirmed_email}. Should I book that?"
        )

        return BookingIdentityFlowResult(
            intent="booking_confirmation_required",
            content=summary,
            chat_context_updates=updates,
            hold_id=hold_id,
        )

    def _identity_not_resolved_reply(
        self,
        updates: dict[str, Any],
        *,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        return BookingIdentityFlowResult(
            intent="booking_conflict",
            content=(
                "I could not verify those patient details yet. "
                "Please try again or contact the clinic for assistance."
            ),
            chat_context_updates=updates,
            hold_id=hold_id,
            booking_attempted=True,
        )


def _parse_seen_before_answer(message: str) -> bool | None:
    normalized = message.lower().strip()
    if any(phrase in normalized for phrase in _SEEN_BEFORE_NO_PHRASES):
        return False
    if any(phrase in normalized for phrase in _SEEN_BEFORE_YES_PHRASES):
        return True
    return None


def _is_valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _dob_ambiguity_issue(
    understanding: ChatTurnUnderstandingResult,
) -> FieldIssue | None:
    """Decide whether the interpreted turn carries an ambiguous date of birth.

    The backend is authoritative: when the interpreter flags an ambiguous
    numeric DOB we regenerate the clarification wording from the raw source so it
    always names the correct candidate dates, and we independently re-validate
    the extracted raw DOB so an interpreter that silently normalized an ambiguous
    value still triggers clarification.
    """
    for issue in understanding.ambiguous_fields:
        if issue.field == "date_of_birth":
            refreshed = detect_ambiguous_numeric_dob(issue.source_text)
            return refreshed if refreshed is not None else issue
    return detect_ambiguous_numeric_dob(understanding.extracted_fields.date_of_birth_raw)


def _merge_parsed_fields(
    deterministic: ParsedPatientFields,
    ctu: ParsedPatientFields,
) -> ParsedPatientFields:
    return ParsedPatientFields(
        full_name=ctu.full_name or deterministic.full_name,
        date_of_birth=ctu.date_of_birth or deterministic.date_of_birth,
        email=ctu.email or deterministic.email,
        phone=deterministic.phone,
    )


def _looks_like_scheduling_text(value: str) -> bool:
    normalized = value.lower()
    scheduling_terms = (
        "dr.",
        "doctor",
        "specialt",
        "availab",
        "appointment",
        "schedule",
        "book",
    )
    if any(term in normalized for term in scheduling_terms):
        return True

    return bool(re.search(r"\b\d{4}-\d{2}-\d{2}\b", value))


def _format_summary_date(booking_context: dict[str, Any]) -> str | None:
    start_time_raw = booking_context.get("selected_start_time")
    if isinstance(start_time_raw, str):
        try:
            return datetime.fromisoformat(start_time_raw).date().isoformat()
        except ValueError:
            pass

    requested_date = booking_context.get("requested_date")
    if isinstance(requested_date, str) and requested_date.strip():
        return requested_date.strip()

    return None


def _format_summary_time(
    booking_context: dict[str, Any],
    *,
    clinic_timezone: ZoneInfo | None = None,
) -> str | None:
    start_time_raw = booking_context.get("selected_start_time")
    if isinstance(start_time_raw, str):
        try:
            parsed = datetime.fromisoformat(start_time_raw)
            if clinic_timezone is not None:
                return format_clinic_local_time_label(parsed, clinic_timezone)
            return parsed.strftime("%H:%M")
        except ValueError:
            pass

    return None


def _collect_summary_facts(
    *,
    booking_context: dict[str, Any],
    confirmed_email: str,
    clinic_timezone: ZoneInfo | None = None,
) -> _SummaryFacts | None:
    doctor_name_raw = booking_context.get("selected_doctor_name")
    if not isinstance(doctor_name_raw, str) or not doctor_name_raw.strip():
        return None

    doctor_name = doctor_name_raw.strip()
    appointment_date = _format_summary_date(booking_context)
    appointment_time = _format_summary_time(
        booking_context,
        clinic_timezone=clinic_timezone,
    )
    if appointment_date is None or appointment_time is None:
        return None

    specialty_name = booking_context.get("selected_specialty_name")
    appointment_label = (
        specialty_name.strip()
        if isinstance(specialty_name, str) and specialty_name.strip()
        else "appointment"
    )

    patient_name_raw = (
        booking_context.get("patient_display_name")
        or (booking_context.get("patient_identity") or {}).get("full_name")
    )
    if not isinstance(patient_name_raw, str) or not patient_name_raw.strip():
        return None

    patient_name = normalize_patient_display_name(patient_name_raw)
    if not patient_name:
        return None

    return _SummaryFacts(
        appointment_label=appointment_label,
        doctor_name=doctor_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        patient_name=patient_name,
        confirmed_email=normalize_email_address(confirmed_email),
    )
