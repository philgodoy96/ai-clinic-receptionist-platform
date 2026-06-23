from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.ai.reliability import MIN_ACCEPTED_CONFIDENCE

_HUMAN_REQUEST_PHRASES = (
    "human",
    "person",
    "real person",
    "receptionist",
    "representative",
    "someone",
)
_HUMAN_REQUEST_INTENT_WORDS = (
    "talk",
    "speak",
    "transfer",
    "connect",
    "call",
    "not helping",
)
_EMERGENCY_KEYWORDS = (
    "emergency",
    "urgent",
    "chest pain",
    "can't breathe",
    "cannot breathe",
)
_BOOKING_CONFLICT_INTENTS = frozenset({"booking_conflict", "hold_conflict"})


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
                "repeated_booking_conflict_count": (self.signals.repeated_booking_conflict_count),
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


def _detect_explicit_human_request(message: str) -> bool:
    normalized = message.lower()
    has_human_phrase = any(phrase in normalized for phrase in _HUMAN_REQUEST_PHRASES)
    has_intent_word = any(word in normalized for word in _HUMAN_REQUEST_INTENT_WORDS)
    return has_human_phrase and has_intent_word


def _message_metadata(message: Any) -> dict[str, Any]:
    metadata = getattr(message, "message_metadata", None)
    if isinstance(metadata, dict):
        return metadata
    return {}


def _message_role(message: Any) -> str:
    role = getattr(message, "role", None)
    if role is None:
        return ""
    if hasattr(role, "value"):
        return str(role.value)
    return str(role)


def _metadata_intent(metadata: dict[str, Any]) -> str | None:
    intent = metadata.get("intent")
    if isinstance(intent, str) and intent:
        return intent
    return None


def _shadow_analysis(metadata: dict[str, Any]) -> dict[str, Any]:
    shadow = metadata.get("llm_shadow_analysis")
    if isinstance(shadow, dict):
        return shadow
    return {}


def _slot_filling(metadata: dict[str, Any]) -> dict[str, Any]:
    slot_filling = metadata.get("slot_filling")
    if isinstance(slot_filling, dict):
        return slot_filling
    return {}


def _context_has_value(context: dict[str, Any], key: str) -> bool:
    value = context.get(key)
    return value is not None and value != ""


def _contains_emergency_keywords(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in _EMERGENCY_KEYWORDS)


def _is_fallback_message(metadata: dict[str, Any]) -> bool:
    if _metadata_intent(metadata) == "fallback":
        return True
    shadow = _shadow_analysis(metadata)
    return shadow.get("intent") == "fallback"


def _is_low_confidence_message(metadata: dict[str, Any]) -> bool:
    shadow = _shadow_analysis(metadata)
    if shadow.get("failure_reason") == "low_confidence":
        return True
    confidence = shadow.get("confidence")
    if isinstance(confidence, (int, float)):
        return float(confidence) < MIN_ACCEPTED_CONFIDENCE
    return False


def _used_provider_fallback(metadata: dict[str, Any]) -> bool:
    return _shadow_analysis(metadata).get("used_fallback") is True


def _slot_filling_rejection_total(metadata: dict[str, Any]) -> int:
    rejected_fields = _slot_filling(metadata).get("rejected_fields")
    if isinstance(rejected_fields, list):
        return len(rejected_fields)
    return 0


def _is_emergency_message(metadata: dict[str, Any]) -> bool:
    return _metadata_intent(metadata) == "emergency"


def _has_medical_emergency_flag(metadata: dict[str, Any]) -> bool:
    safety_flags = _shadow_analysis(metadata).get("safety_flags")
    if isinstance(safety_flags, list):
        return "medical_emergency" in safety_flags
    return False


def _is_booking_conflict_message(metadata: dict[str, Any]) -> bool:
    intent = _metadata_intent(metadata)
    return intent in _BOOKING_CONFLICT_INTENTS if intent is not None else False


def _repeated_assistant_intent(recent_messages: list[Any]) -> tuple[str | None, int]:
    last_intent: str | None = None
    repeated_intent_count = 0

    for message in reversed(recent_messages):
        if _message_role(message) != "assistant":
            continue
        intent = _metadata_intent(_message_metadata(message))
        if last_intent is None:
            if intent is None:
                return None, 0
            last_intent = intent
            repeated_intent_count = 1
            continue
        if intent != last_intent:
            break
        repeated_intent_count += 1

    return last_intent, repeated_intent_count


class ConversationHealthService:
    def evaluate(
        self,
        *,
        user_message: str,
        chat_context: dict[str, Any],
        recent_messages: list[Any],
    ) -> ConversationHealthResult:
        safe_context = chat_context if isinstance(chat_context, dict) else {}

        message_count = len(recent_messages)
        user_message_count = 0
        assistant_message_count = 0
        fallback_count = 0
        low_confidence_count = 0
        provider_fallback_count = 0
        slot_filling_rejection_count = 0
        emergency_signal_count = 0
        repeated_booking_conflict_count = 0

        for message in recent_messages:
            role = _message_role(message)
            metadata = _message_metadata(message)

            if role == "user":
                user_message_count += 1
                if _is_emergency_message(metadata):
                    emergency_signal_count += 1
            elif role == "assistant":
                assistant_message_count += 1
                if _is_fallback_message(metadata):
                    fallback_count += 1
                if _is_low_confidence_message(metadata):
                    low_confidence_count += 1
                if _used_provider_fallback(metadata):
                    provider_fallback_count += 1
                slot_filling_rejection_count += _slot_filling_rejection_total(metadata)
                if _is_emergency_message(metadata):
                    emergency_signal_count += 1
                if _has_medical_emergency_flag(metadata):
                    emergency_signal_count += 1
                if _is_booking_conflict_message(metadata):
                    repeated_booking_conflict_count += 1

        if _contains_emergency_keywords(user_message):
            emergency_signal_count += 1

        explicit_human_request = _detect_explicit_human_request(user_message)
        active_hold_present = _context_has_value(
            safe_context, "hold_id"
        ) and not _context_has_value(safe_context, "appointment_id")
        booking_confirmed = _context_has_value(
            safe_context, "appointment_id"
        ) or _context_has_value(safe_context, "booking_confirmed_at")

        last_intent, repeated_intent_count = _repeated_assistant_intent(recent_messages)

        signals = ConversationHealthSignals(
            message_count=message_count,
            user_message_count=user_message_count,
            assistant_message_count=assistant_message_count,
            fallback_count=fallback_count,
            slot_filling_rejection_count=slot_filling_rejection_count,
            low_confidence_count=low_confidence_count,
            provider_fallback_count=provider_fallback_count,
            emergency_signal_count=emergency_signal_count,
            repeated_booking_conflict_count=repeated_booking_conflict_count,
            explicit_human_request=explicit_human_request,
            active_hold_present=active_hold_present,
            booking_confirmed=booking_confirmed,
            last_intent=last_intent,
            repeated_intent_count=repeated_intent_count,
        )

        notes: list[str] = []
        should_escalate_immediately = False
        should_suggest_escalation = False
        escalation_reason = EscalationReason.NONE

        if explicit_human_request:
            should_escalate_immediately = True
            escalation_reason = EscalationReason.USER_REQUESTED_HUMAN
            notes.append("user_explicitly_requested_human")
        elif emergency_signal_count > 0:
            should_escalate_immediately = True
            escalation_reason = EscalationReason.MEDICAL_EMERGENCY
            notes.append("emergency_signal_detected")

        if not should_escalate_immediately:
            if fallback_count >= 3:
                should_suggest_escalation = True
                escalation_reason = EscalationReason.REPEATED_FALLBACK
                notes.append("fallback_threshold_reached")
            elif slot_filling_rejection_count >= 3:
                should_suggest_escalation = True
                escalation_reason = EscalationReason.REPEATED_SLOT_FILLING_REJECTION
                notes.append("slot_filling_rejection_threshold_reached")
            elif low_confidence_count >= 3:
                should_suggest_escalation = True
                escalation_reason = EscalationReason.REPEATED_LOW_CONFIDENCE
            elif repeated_booking_conflict_count >= 2:
                should_suggest_escalation = True
                escalation_reason = EscalationReason.REPEATED_BOOKING_CONFLICT
            elif message_count >= 12 and fallback_count >= 2 and not booking_confirmed:
                should_suggest_escalation = True
                escalation_reason = EscalationReason.NO_PROGRESS
                notes.append("message_count_signal_requires_no_progress_context")

        return ConversationHealthResult(
            signals=signals,
            should_suggest_escalation=should_suggest_escalation,
            should_escalate_immediately=should_escalate_immediately,
            escalation_reason=escalation_reason,
            notes=notes,
        )
