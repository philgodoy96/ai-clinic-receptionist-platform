from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistIntent,
    ChatReceptionistService,
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
)

_INTERNAL_TERMS: tuple[str, ...] = (
    "patient_resolution_id",
    "hold_id",
    "slot_id",
    "redis",
    "database",
    "backend error",
)

_CLARIFICATION_KEYWORDS: tuple[str, ...] = (
    "yyyy-mm-dd",
    "tomorrow",
    "specific date",
    "next monday",
    "clarif",
)


@dataclass(frozen=True, slots=True)
class ChatSchedulingEvaluationStep:
    user_message: str
    expected_intent: ChatReceptionistIntent | None = None
    expected_booking_confirmed: bool | None = None
    expected_appointment_created: bool | None = None
    expected_reply_contains_any: tuple[str, ...] = ()
    expected_reply_not_contains_any: tuple[str, ...] = ()
    expect_no_internal_identifiers: bool = True
    expect_no_invented_email: bool = True
    expect_clarification_wording: bool = False


@dataclass(frozen=True, slots=True)
class ChatSchedulingEvaluationScenario:
    name: str
    description: str
    steps: tuple[ChatSchedulingEvaluationStep, ...]


@dataclass(frozen=True, slots=True)
class ChatSchedulingExpectationResult:
    field: str
    passed: bool
    expected: Any
    actual: Any
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ChatSchedulingStepResult:
    step_index: int
    user_message: str
    passed: bool
    conversation_id: UUID
    intent: ChatReceptionistIntent
    reply: str
    booking_confirmed: bool
    appointment_id: UUID | None
    expectation_results: tuple[ChatSchedulingExpectationResult, ...]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChatSchedulingScenarioResult:
    scenario_name: str
    passed: bool
    conversation_id: UUID | None
    step_results: tuple[ChatSchedulingStepResult, ...]
    failure_reason: str | None = None


def reply_contains_uuid(reply: str) -> bool:
    return _UUID_PATTERN.search(reply) is not None


def reply_contains_internal_terms(reply: str) -> tuple[str, ...]:
    normalized = reply.lower()
    return tuple(term for term in _INTERNAL_TERMS if term in normalized)


def extract_emails(text: str) -> frozenset[str]:
    return frozenset(match.group(0).lower() for match in _EMAIL_PATTERN.finditer(text))


def reply_contains_clarification_wording(reply: str) -> bool:
    normalized = reply.lower()
    return any(keyword in normalized for keyword in _CLARIFICATION_KEYWORDS)


def evaluate_no_internal_identifiers(*, reply: str) -> ChatSchedulingExpectationResult:
    internal_terms = reply_contains_internal_terms(reply)
    has_uuid = reply_contains_uuid(reply)
    passed = not internal_terms and not has_uuid
    actual: list[str] = []
    if has_uuid:
        actual.append("uuid")
    actual.extend(internal_terms)
    return ChatSchedulingExpectationResult(
        field="no_internal_identifiers",
        passed=passed,
        expected="no UUIDs or internal terms",
        actual=actual or None,
        message=None if passed else "reply exposed internal identifiers",
    )


def evaluate_no_invented_email(
    *,
    reply: str,
    user_messages: tuple[str, ...],
) -> ChatSchedulingExpectationResult:
    allowed_emails: set[str] = set()
    for message in user_messages:
        allowed_emails.update(extract_emails(message))

    reply_emails = extract_emails(reply)
    invented = sorted(email for email in reply_emails if email not in allowed_emails)
    passed = not invented
    expected_value: Any
    if allowed_emails:
        expected_value = sorted(allowed_emails)
    else:
        expected_value = "no reply emails unless user provided one"
    return ChatSchedulingExpectationResult(
        field="no_invented_email",
        passed=passed,
        expected=expected_value,
        actual=sorted(reply_emails) if reply_emails else None,
        message=None if passed else f"reply invented email(s): {', '.join(invented)}",
    )


def evaluate_reply_contains_any(
    *,
    reply: str,
    phrases: tuple[str, ...],
) -> ChatSchedulingExpectationResult:
    if not phrases:
        return ChatSchedulingExpectationResult(
            field="reply_contains_any",
            passed=True,
            expected=(),
            actual=None,
        )

    normalized_reply = reply.lower()
    matched = tuple(phrase for phrase in phrases if phrase.lower() in normalized_reply)
    passed = bool(matched)
    return ChatSchedulingExpectationResult(
        field="reply_contains_any",
        passed=passed,
        expected=phrases,
        actual=matched or None,
        message=None if passed else "reply did not contain any expected phrase",
    )


def evaluate_reply_not_contains_any(
    *,
    reply: str,
    phrases: tuple[str, ...],
) -> ChatSchedulingExpectationResult:
    if not phrases:
        return ChatSchedulingExpectationResult(
            field="reply_not_contains_any",
            passed=True,
            expected=(),
            actual=None,
        )

    normalized_reply = reply.lower()
    forbidden_matches = tuple(
        phrase for phrase in phrases if phrase.lower() in normalized_reply
    )
    passed = not forbidden_matches
    return ChatSchedulingExpectationResult(
        field="reply_not_contains_any",
        passed=passed,
        expected=phrases,
        actual=forbidden_matches or None,
        message=None if passed else "reply contained forbidden phrase(s)",
    )


def evaluate_clarification_wording(*, reply: str) -> ChatSchedulingExpectationResult:
    passed = reply_contains_clarification_wording(reply)
    return ChatSchedulingExpectationResult(
        field="clarification_wording",
        passed=passed,
        expected=True,
        actual=passed,
        message=None if passed else "reply did not contain clarification wording",
    )


def evaluate_expected_intent(
    *,
    actual: ChatReceptionistIntent,
    expected: ChatReceptionistIntent,
) -> ChatSchedulingExpectationResult:
    passed = actual == expected
    return ChatSchedulingExpectationResult(
        field="intent",
        passed=passed,
        expected=expected,
        actual=actual,
    )


def evaluate_expected_booking_confirmed(
    *,
    actual: bool,
    expected: bool,
) -> ChatSchedulingExpectationResult:
    passed = actual == expected
    return ChatSchedulingExpectationResult(
        field="booking_confirmed",
        passed=passed,
        expected=expected,
        actual=actual,
    )


def evaluate_expected_appointment_created(
    *,
    appointment_id: UUID | None,
    expected_created: bool,
) -> ChatSchedulingExpectationResult:
    actual_created = appointment_id is not None
    passed = actual_created == expected_created
    return ChatSchedulingExpectationResult(
        field="appointment_created",
        passed=passed,
        expected=expected_created,
        actual=actual_created,
    )


def evaluate_chat_scheduling_step(
    *,
    step: ChatSchedulingEvaluationStep,
    result: ChatMessageResult,
    prior_user_messages: tuple[str, ...],
) -> tuple[ChatSchedulingExpectationResult, ...]:
    user_messages = (*prior_user_messages, step.user_message)
    expectations: list[ChatSchedulingExpectationResult] = []

    if step.expect_no_internal_identifiers:
        expectations.append(evaluate_no_internal_identifiers(reply=result.reply))

    if step.expect_no_invented_email:
        expectations.append(
            evaluate_no_invented_email(
                reply=result.reply,
                user_messages=user_messages,
            ),
        )

    if step.expected_intent is not None:
        expectations.append(
            evaluate_expected_intent(
                actual=result.intent,
                expected=step.expected_intent,
            ),
        )

    if step.expected_booking_confirmed is not None:
        expectations.append(
            evaluate_expected_booking_confirmed(
                actual=result.booking_confirmed,
                expected=step.expected_booking_confirmed,
            ),
        )

    if step.expected_appointment_created is not None:
        expectations.append(
            evaluate_expected_appointment_created(
                appointment_id=result.appointment_id,
                expected_created=step.expected_appointment_created,
            ),
        )

    if step.expected_reply_contains_any:
        expectations.append(
            evaluate_reply_contains_any(
                reply=result.reply,
                phrases=step.expected_reply_contains_any,
            ),
        )

    if step.expected_reply_not_contains_any:
        expectations.append(
            evaluate_reply_not_contains_any(
                reply=result.reply,
                phrases=step.expected_reply_not_contains_any,
            ),
        )

    if step.expect_clarification_wording:
        expectations.append(evaluate_clarification_wording(reply=result.reply))

    return tuple(expectations)


def run_chat_scheduling_scenario(
    *,
    service: ChatReceptionistService,
    scenario: ChatSchedulingEvaluationScenario,
    patient_id: UUID | None = None,
    initial_conversation_metadata: Mapping[str, Any] | None = None,
) -> ChatSchedulingScenarioResult:
    conversation_id: UUID | None = None
    prior_user_messages: list[str] = []
    step_results: list[ChatSchedulingStepResult] = []

    for step_index, step in enumerate(scenario.steps):
        payload = ChatMessageInput(
            message=step.user_message,
            conversation_id=conversation_id,
            patient_id=patient_id,
            conversation_metadata=dict(initial_conversation_metadata or {}),
        )
        result = service.handle_message(payload)
        conversation_id = result.conversation.id

        expectation_results = evaluate_chat_scheduling_step(
            step=step,
            result=result,
            prior_user_messages=tuple(prior_user_messages),
        )
        passed = all(item.passed for item in expectation_results)
        failure_reason = next(
            (
                item.message or item.field
                for item in expectation_results
                if not item.passed
            ),
            None,
        )

        step_results.append(
            ChatSchedulingStepResult(
                step_index=step_index,
                user_message=step.user_message,
                passed=passed,
                conversation_id=result.conversation.id,
                intent=result.intent,
                reply=result.reply,
                booking_confirmed=result.booking_confirmed,
                appointment_id=result.appointment_id,
                expectation_results=expectation_results,
                failure_reason=failure_reason,
            ),
        )
        prior_user_messages.append(step.user_message)

    scenario_passed = all(step.passed for step in step_results)
    scenario_failure_reason = next(
        (step.failure_reason for step in step_results if not step.passed),
        None,
    )

    return ChatSchedulingScenarioResult(
        scenario_name=scenario.name,
        passed=scenario_passed,
        conversation_id=conversation_id,
        step_results=tuple(step_results),
        failure_reason=scenario_failure_reason,
    )
