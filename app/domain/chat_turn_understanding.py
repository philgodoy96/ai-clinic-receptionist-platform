from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ChatTurnUnderstandingInterpreterProvider(StrEnum):
    DISABLED = "disabled"
    FAKE = "fake"
    GROQ = "groq"


class ConversationState(StrEnum):
    IDLE = "idle"
    COLLECTING_APPOINTMENT_REQUEST = "collecting_appointment_request"
    OFFERING_SLOTS = "offering_slots"
    HOLD_ACTIVE = "hold_active"
    COLLECTING_PATIENT_STATUS = "collecting_patient_status"
    COLLECTING_PATIENT_IDENTITY = "collecting_patient_identity"
    CONFIRMING_EMAIL = "confirming_email"
    CONFIRMING_BOOKING = "confirming_booking"
    CANCELLATION_INTAKE = "cancellation_intake"
    RESCHEDULE_INTAKE = "reschedule_intake"
    APPOINTMENT_LOOKUP_INTAKE = "appointment_lookup_intake"
    ESCALATED = "escalated"


class ExpectedResponseType(StrEnum):
    OPEN_TEXT = "open_text"
    YES_NO = "yes_no"
    PATIENT_STATUS = "patient_status"
    PATIENT_IDENTITY = "patient_identity"
    EMAIL_CONFIRMATION = "email_confirmation"
    BOOKING_CONFIRMATION = "booking_confirmation"
    SLOT_SELECTION = "slot_selection"
    DATE_OR_TIME = "date_or_time"
    SPECIALTY_OR_DOCTOR = "specialty_or_doctor"


class ChatTurnIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    AVAILABILITY_REQUEST = "availability_request"
    SLOT_SELECTION = "slot_selection"
    CONFIRMATION = "confirmation"
    PATIENT_STATUS_ANSWER = "patient_status_answer"
    PATIENT_IDENTITY_PROVIDED = "patient_identity_provided"
    CHANGE_REQUEST = "change_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    LIST_APPOINTMENTS = "list_appointments"
    HUMAN_ESCALATION_REQUEST = "human_escalation_request"
    EMERGENCY = "emergency"
    FALLBACK = "fallback"


class ConfirmationDecision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    WANTS_CHANGE = "wants_change"
    UNCLEAR = "unclear"
    NOT_APPLICABLE = "not_applicable"


class PatientStatusAnswer(StrEnum):
    EXISTING_PATIENT = "existing_patient"
    NEW_PATIENT = "new_patient"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class FieldIssue(BaseModel):
    field: str
    source_text: str | None = None
    reason: str
    candidates: list[Any] = Field(default_factory=list)
    clarification_question: str | None = None


class ExtractedTurnFields(BaseModel):
    patient_name: str | None = None
    date_of_birth: str | None = None
    date_of_birth_raw: str | None = None
    email: str | None = None
    phone: str | None = None
    specialty: str | None = None
    specialty_raw: str | None = None
    doctor_name: str | None = None
    doctor_name_raw: str | None = None
    appointment_date: str | None = None
    appointment_date_raw: str | None = None
    appointment_time: str | None = None
    appointment_time_raw: str | None = None
    appointment_time_window: str | None = None
    appointment_time_window_raw: str | None = None


class OfferedSlot(BaseModel):
    reference: str
    start_time: str
    doctor_id: str | None = None
    doctor_name: str | None = None
    specialty_id: str | None = None
    specialty_name: str | None = None
    display_label: str | None = None


class KnownDoctor(BaseModel):
    id: str
    full_name: str
    specialty_id: str | None = None
    aliases: list[str] = Field(default_factory=list)


class KnownSpecialty(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)


class ChatTurnUnderstandingRequest(BaseModel):
    conversation_state: ConversationState
    expected_response_type: ExpectedResponseType
    last_assistant_question: str | None = None
    current_context: dict[str, Any] = Field(default_factory=dict)
    latest_user_message: str
    allowed_intents: list[ChatTurnIntent]
    offered_slots: list[OfferedSlot] = Field(default_factory=list)
    known_specialties: list[KnownSpecialty] = Field(default_factory=list)
    known_doctors: list[KnownDoctor] = Field(default_factory=list)
    locale: str | None = None


class ChatTurnUnderstandingResult(BaseModel):
    intent: ChatTurnIntent
    confirmation_decision: ConfirmationDecision = ConfirmationDecision.NOT_APPLICABLE
    patient_status_answer: PatientStatusAnswer = PatientStatusAnswer.NOT_APPLICABLE
    extracted_fields: ExtractedTurnFields = Field(default_factory=ExtractedTurnFields)
    missing_fields: list[FieldIssue] = Field(default_factory=list)
    ambiguous_fields: list[FieldIssue] = Field(default_factory=list)
    selected_slot_reference: str | None = None
    clarification_question: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
