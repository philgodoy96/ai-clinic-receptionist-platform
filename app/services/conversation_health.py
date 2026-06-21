from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EscalationReason(StrEnum):
    NONE = "none"
    USER_REQUESTED_HUMAN = "user_requested_human"
    MEDICAL_EMERGENCY = "medical_emergency"
    REPEATED_FALLBACK = "repeated_fallback"
    REPEATED_SLOT_FILLING_REJECTION = "repeated_slot_filling_rejection"
    REPEATED_LOW_CONFIDENCE = "repeated_low_confidence"
    REPEATED_BOOKING_CONFLICT = "repeated_booking_conflict"
    NO_PROGRESS = "no_progress"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ConversationHealthSignals:
    message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0
    fallback_count: int = 0
    slot_filling_rejection_count: int = 0
    low_confidence_count: int = 0
    provider_fallback_count: int = 0
    emergency_signal_count: int = 0
    repeated_booking_conflict_count: int = 0
    explicit_human_request: bool = False
    active_hold_present: bool = False
    booking_confirmed: bool = False
    last_intent: str | None = None
    repeated_intent_count: int = 0


@dataclass(frozen=True, slots=True)
class ConversationHealthResult:
    signals: ConversationHealthSignals
    should_suggest_escalation: bool = False
    should_escalate_immediately: bool = False
    escalation_reason: EscalationReason = EscalationReason.NONE
    notes: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "signals": {
                "message_count": self.signals.message_count,
                "user_message_count": self.signals.user_message_count,
                "assistant_message_count": self.signals.assistant_message_count,
                "fallback_count": self.signals.fallback_count,
                "slot_filling_rejection_count": self.signals.slot_filling_rejection_count,
                "low_confidence_count": self.signals.low_confidence_count,
                "provider_fallback_count": self.signals.provider_fallback_count,
                "emergency_signal_count": self.signals.emergency_signal_count,
                "repeated_booking_conflict_count": (
                    self.signals.repeated_booking_conflict_count
                ),
                "explicit_human_request": self.signals.explicit_human_request,
                "active_hold_present": self.signals.active_hold_present,
                "booking_confirmed": self.signals.booking_confirmed,
                "last_intent": self.signals.last_intent,
                "repeated_intent_count": self.signals.repeated_intent_count,
            },
            "should_suggest_escalation": self.should_suggest_escalation,
            "should_escalate_immediately": self.should_escalate_immediately,
            "escalation_reason": self.escalation_reason.value,
            "notes": self.notes,
        }


class ConversationHealthService:
    def evaluate(
        self,
        *,
        user_message: str,
        chat_context: dict[str, Any],
        recent_messages: list[Any],
    ) -> ConversationHealthResult:
        return ConversationHealthResult(signals=ConversationHealthSignals())