from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
)

APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE = "reschedule"

RESCHEDULE_IDENTITY_ENTRY_MESSAGE = (
    "Of course. I can look it up first. What is the patient's full name and date of birth?"
)
RESCHEDULE_IDENTITY_REPROMPT_MESSAGE = (
    "I still need the patient's full name and date of birth to look up the appointment."
)


@dataclass(frozen=True, slots=True)
class RescheduleFlowResult:
    intent: str
    content: str
    chat_context_updates: dict[str, Any]


class ChatAppointmentReschedulingOrchestrator:
    def handle_patient_identity_intake(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> RescheduleFlowResult:
        del message, chat_context
        return RescheduleFlowResult(
            intent="reschedule_request",
            content=RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
            chat_context_updates={
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
                ),
            },
        )
