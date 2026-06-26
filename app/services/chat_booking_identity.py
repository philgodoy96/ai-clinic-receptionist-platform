from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

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
from app.services.patient_identity_resolution import (
    PatientIdentityResolutionService,
    PatientResolutionNotFoundError,
)

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


class ChatBookingIdentityOrchestrator:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution

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
                updates=updates,
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

    def _already_booked_reply(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        doctor_name = merged_context.get("selected_doctor_name")
        appointment_date = _format_summary_date(merged_context)
        appointment_time = _format_summary_time(merged_context)
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
        updates: dict[str, Any],
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        seen_before = _parse_seen_before_answer(message)
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
            updates["patient_seen_before"] = False
            updates["booking_identity_step"] = ChatBookingIdentityStep.COLLECT_NEW_NAME.value
            return BookingIdentityFlowResult(
                intent="patient_identity_partial",
                content="No problem. What name should I put on the appointment?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content="Have you been seen at this clinic before? Please answer yes or no.",
            chat_context_updates=updates,
            hold_id=hold_id,
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
        parsed = parse_patient_fields(message, booking_context=True)
        if parsed.full_name is None or parsed.date_of_birth is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content=(
                    "I still need your full name and date of birth to look up your profile. "
                    "Your hold is still active."
                ),
                chat_context_updates=updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        full_name = normalize_patient_display_name(parsed.full_name)
        updates["patient_identity"] = {
            **(booking_context.get("patient_identity") or {}),
            "full_name": full_name,
            "date_of_birth": parsed.date_of_birth,
        }
        if parsed.phone:
            updates["patient_identity"]["phone"] = parsed.phone
        if parsed.email:
            updates["patient_identity"]["email"] = normalize_email_address(parsed.email)

        result = self.patient_identity_resolution.resolve(
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
        parsed = parse_patient_fields(message, booking_context=True)
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
        parsed = parse_patient_fields(message, booking_context=True)
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
        booking_context: dict[str, Any],
        updates: dict[str, Any],
        parse_patient_fields: Any,
        hold_id: str,
    ) -> BookingIdentityFlowResult:
        parsed = parse_patient_fields(message, booking_context=True)
        if parsed.email is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        email = normalize_email_address(parsed.email)
        updates["pending_confirmation_email"] = email
        updates["booking_identity_step"] = ChatBookingIdentityStep.CONFIRM_NEW_EMAIL.value
        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content=f"I heard {email} — is that correct?",
            chat_context_updates=updates,
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
        parsed = parse_patient_fields(message, booking_context=True)
        if parsed.email is None:
            return BookingIdentityFlowResult(
                intent="booking_identity_missing",
                content="What email should we use for the appointment confirmation?",
                chat_context_updates=updates,
                hold_id=hold_id,
            )

        email = normalize_email_address(parsed.email)
        updates["pending_confirmation_email"] = email
        updates["booking_identity_step"] = (
            ChatBookingIdentityStep.CONFIRM_CONFIRMATION_EMAIL.value
        )
        return BookingIdentityFlowResult(
            intent="patient_identity_partial",
            content=f"I heard {email} — is that correct?",
            chat_context_updates=updates,
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


def _format_summary_time(booking_context: dict[str, Any]) -> str | None:
    start_time_raw = booking_context.get("selected_start_time")
    if isinstance(start_time_raw, str):
        try:
            return datetime.fromisoformat(start_time_raw).strftime("%H:%M")
        except ValueError:
            pass

    return None


def _collect_summary_facts(
    *,
    booking_context: dict[str, Any],
    confirmed_email: str,
) -> _SummaryFacts | None:
    doctor_name_raw = booking_context.get("selected_doctor_name")
    if not isinstance(doctor_name_raw, str) or not doctor_name_raw.strip():
        return None

    doctor_name = doctor_name_raw.strip()
    appointment_date = _format_summary_date(booking_context)
    appointment_time = _format_summary_time(booking_context)
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
