from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from app.services.conversation_health import (
    ConversationHealthService,
    EscalationReason,
    detect_explicit_human_request,
)


@dataclass
class FakeConversationMessage:
    role: str
    message_metadata: dict[str, Any] = field(default_factory=dict)


@pytest.fixture()
def health_service() -> ConversationHealthService:
    return ConversationHealthService()


def _assistant_message(
    *,
    intent: str,
    slot_filling: dict[str, Any] | None = None,
    llm_shadow_analysis: dict[str, Any] | None = None,
) -> FakeConversationMessage:
    metadata: dict[str, Any] = {"intent": intent}
    if slot_filling is not None:
        metadata["slot_filling"] = slot_filling
    if llm_shadow_analysis is not None:
        metadata["llm_shadow_analysis"] = llm_shadow_analysis
    return FakeConversationMessage(role="assistant", message_metadata=metadata)


def _user_message(content: str = "hello") -> FakeConversationMessage:
    return FakeConversationMessage(role="user", message_metadata={"content": content})


def _alternating_success_messages(count: int) -> list[FakeConversationMessage]:
    messages: list[FakeConversationMessage] = []
    successful_intents = (
        "list_specialties",
        "list_doctors",
        "greeting",
        "appointment_request",
    )
    for index in range(count):
        if index % 2 == 0:
            messages.append(_user_message(f"message {index}"))
        else:
            intent = successful_intents[(index // 2) % len(successful_intents)]
            messages.append(_assistant_message(intent=intent))
    return messages


def test_explicit_human_request_escalates_immediately(
    health_service: ConversationHealthService,
) -> None:
    result = health_service.evaluate(
        user_message="Can I speak to a real person?",
        chat_context={},
        recent_messages=[],
    )

    assert result.should_escalate_immediately is True
    assert result.escalation_reason == EscalationReason.USER_REQUESTED_HUMAN
    assert result.signals.explicit_human_request is True


@pytest.mark.parametrize(
    "message",
    [
        "Human please",
        "I need a real person.",
        "Can I speak with someone?",
        "Transfer me to a receptionist.",
        "Representative please.",
        "Can a real person help me?",
    ],
)
def test_broadened_explicit_human_request_phrases_escalate(
    health_service: ConversationHealthService,
    message: str,
) -> None:
    result = health_service.evaluate(
        user_message=message,
        chat_context={},
        recent_messages=[],
    )

    assert result.should_escalate_immediately is True
    assert result.escalation_reason == EscalationReason.USER_REQUESTED_HUMAN
    assert result.signals.explicit_human_request is True


def test_academic_human_mention_does_not_escalate(
    health_service: ConversationHealthService,
) -> None:
    result = health_service.evaluate(
        user_message="My doctor is a real person and very kind.",
        chat_context={},
        recent_messages=[],
    )

    assert result.should_escalate_immediately is False
    assert result.signals.explicit_human_request is False


def test_detect_explicit_human_request_unit_cases() -> None:
    assert detect_explicit_human_request("Human please") is True
    assert detect_explicit_human_request("I need a real person.") is True
    assert detect_explicit_human_request("My doctor is a real person") is False


def test_emergency_escalates_immediately(
    health_service: ConversationHealthService,
) -> None:
    result = health_service.evaluate(
        user_message="This is an emergency",
        chat_context={},
        recent_messages=[],
    )

    assert result.should_escalate_immediately is True
    assert result.escalation_reason == EscalationReason.MEDICAL_EMERGENCY
    assert result.signals.emergency_signal_count >= 1


def test_message_count_alone_does_not_escalate(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = _alternating_success_messages(20)

    result = health_service.evaluate(
        user_message="What specialties do you offer?",
        chat_context={},
        recent_messages=recent_messages,
    )

    assert result.should_escalate_immediately is False
    assert result.should_suggest_escalation is False
    assert result.signals.message_count == 20
    assert result.signals.fallback_count == 0


def test_repeated_fallback_suggests_escalation(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = [
        _user_message("unclear 1"),
        _assistant_message(intent="fallback"),
        _user_message("unclear 2"),
        _assistant_message(intent="fallback"),
        _user_message("unclear 3"),
        _assistant_message(intent="fallback"),
    ]

    result = health_service.evaluate(
        user_message="still unclear",
        chat_context={},
        recent_messages=recent_messages,
    )

    assert result.should_suggest_escalation is True
    assert result.should_escalate_immediately is False
    assert result.escalation_reason == EscalationReason.REPEATED_FALLBACK
    assert result.signals.fallback_count == 3


def test_repeated_slot_filling_rejections_suggest_escalation(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = [
        _user_message("booking info"),
        _assistant_message(
            intent="patient_identity_partial",
            slot_filling={"rejected_fields": [{"field": "full_name", "reason": "low_confidence"}]},
        ),
        _user_message("more info"),
        _assistant_message(
            intent="patient_identity_partial",
            slot_filling={"rejected_fields": [{"field": "phone", "reason": "invalid"}]},
        ),
        _user_message("again"),
        _assistant_message(
            intent="patient_identity_partial",
            slot_filling={
                "rejected_fields": [
                    {"field": "email", "reason": "invalid"},
                    {"field": "date_of_birth", "reason": "invalid"},
                ],
            },
        ),
    ]

    result = health_service.evaluate(
        user_message="Jane Doe",
        chat_context={},
        recent_messages=recent_messages,
    )

    assert result.should_suggest_escalation is True
    assert result.escalation_reason == EscalationReason.REPEATED_SLOT_FILLING_REJECTION
    assert result.signals.slot_filling_rejection_count >= 3


def test_repeated_low_confidence_suggests_escalation(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = [
        _user_message("msg 1"),
        _assistant_message(
            intent="appointment_request",
            llm_shadow_analysis={"failure_reason": "low_confidence"},
        ),
        _user_message("msg 2"),
        _assistant_message(
            intent="appointment_request",
            llm_shadow_analysis={"failure_reason": "low_confidence"},
        ),
        _user_message("msg 3"),
        _assistant_message(
            intent="appointment_request",
            llm_shadow_analysis={"failure_reason": "low_confidence"},
        ),
    ]

    result = health_service.evaluate(
        user_message="I need help",
        chat_context={},
        recent_messages=recent_messages,
    )

    assert result.should_suggest_escalation is True
    assert result.escalation_reason == EscalationReason.REPEATED_LOW_CONFIDENCE
    assert result.signals.low_confidence_count == 3


def test_repeated_booking_conflicts_suggest_escalation(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = [
        _user_message("hold slot"),
        _assistant_message(intent="hold_conflict"),
        _user_message("try again"),
        _assistant_message(intent="booking_conflict"),
    ]

    result = health_service.evaluate(
        user_message="book please",
        chat_context={},
        recent_messages=recent_messages,
    )

    assert result.should_suggest_escalation is True
    assert result.escalation_reason == EscalationReason.REPEATED_BOOKING_CONFLICT
    assert result.signals.repeated_booking_conflict_count == 2


def test_active_hold_detection(
    health_service: ConversationHealthService,
) -> None:
    hold_id = str(uuid4())

    result = health_service.evaluate(
        user_message="Is my hold still active?",
        chat_context={"hold_id": hold_id},
        recent_messages=[],
    )

    assert result.signals.active_hold_present is True
    assert result.signals.booking_confirmed is False


def test_booking_confirmed_suppresses_no_progress_suggestion(
    health_service: ConversationHealthService,
) -> None:
    recent_messages = [
        *_alternating_success_messages(8),
        _user_message("unclear"),
        _assistant_message(intent="fallback"),
        _user_message("still unclear"),
        _assistant_message(intent="fallback"),
    ]
    assert len(recent_messages) >= 12

    without_booking = health_service.evaluate(
        user_message="another unclear message",
        chat_context={},
        recent_messages=recent_messages,
    )
    assert without_booking.should_suggest_escalation is True
    assert without_booking.escalation_reason == EscalationReason.NO_PROGRESS

    with_booking = health_service.evaluate(
        user_message="another unclear message",
        chat_context={"appointment_id": str(uuid4())},
        recent_messages=recent_messages,
    )
    assert with_booking.should_suggest_escalation is False
    assert with_booking.signals.booking_confirmed is True
