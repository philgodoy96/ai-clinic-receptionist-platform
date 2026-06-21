from enum import StrEnum

from pydantic import BaseModel, Field


class ReceptionistLLMIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    AVAILABILITY_REQUEST = "availability_request"
    HOLD_REQUEST = "hold_request"
    BOOKING_CONFIRMATION = "booking_confirmation"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    HUMAN_ESCALATION_REQUEST = "human_escalation_request"
    EMERGENCY = "emergency"
    FALLBACK = "fallback"


class ReceptionistUrgency(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"
    EMERGENCY = "emergency"


class ExtractedPatientIdentity(BaseModel):
    full_name: str | None = None
    date_of_birth: str | None = None
    phone: str | None = None
    email: str | None = None


class ReceptionistExtractedFields(BaseModel):
    specialty: str | None = None
    doctor_name: str | None = None
    date: str | None = None
    time: str | None = None
    patient_identity: ExtractedPatientIdentity = Field(
        default_factory=ExtractedPatientIdentity,
    )


class ReceptionistLLMAnalysis(BaseModel):
    intent: ReceptionistLLMIntent
    confidence: float = Field(ge=0.0, le=1.0)
    urgency: ReceptionistUrgency
    extracted: ReceptionistExtractedFields = Field(
        default_factory=ReceptionistExtractedFields,
    )
    requires_human: bool = False
    safety_flags: list[str] = Field(default_factory=list)


def fallback_receptionist_analysis() -> ReceptionistLLMAnalysis:
    return ReceptionistLLMAnalysis(
        intent=ReceptionistLLMIntent.FALLBACK,
        confidence=0.0,
        urgency=ReceptionistUrgency.NORMAL,
    )