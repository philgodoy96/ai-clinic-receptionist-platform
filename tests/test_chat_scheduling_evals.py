from __future__ import annotations

import pytest

from app.evals.chat_scheduling import (
    ChatSchedulingEvaluationScenario,
    ChatSchedulingEvaluationStep,
    evaluate_no_internal_identifiers,
    evaluate_no_invented_email,
    extract_emails,
    reply_contains_internal_terms,
    reply_contains_uuid,
    run_chat_scheduling_scenario,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from tests.llm_provider_test_helpers import RaisingLLMProvider
from tests.test_chat_receptionist_service import _create_availability_guidance_service
from tests.test_chat_structured_slot_filling import create_structured_slot_filling_chat_service
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


@pytest.fixture()
def availability_guidance_service() -> ChatReceptionistService:
    service, _repository, _hold_service = _create_availability_guidance_service(
        create_demo_scheduling_service_with_emily_july_availability(),
    )
    return service


def test_multi_turn_scenario_preserves_conversation_id(
    availability_guidance_service: ChatReceptionistService,
) -> None:
    scenario = ChatSchedulingEvaluationScenario(
        name="doctor_then_date",
        description="Doctor selection carries into a follow-up date message.",
        steps=(
            ChatSchedulingEvaluationStep(
                user_message="Dr. Emily Carter availability",
                expected_intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
                expected_booking_confirmed=False,
                expected_appointment_created=False,
            ),
            ChatSchedulingEvaluationStep(
                user_message="2026-07-02",
                expected_intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                expected_booking_confirmed=False,
                expected_appointment_created=False,
                expected_reply_contains_any=("09:00", "10:30"),
            ),
        ),
    )

    result = run_chat_scheduling_scenario(
        service=availability_guidance_service,
        scenario=scenario,
    )

    assert result.passed is True
    assert len(result.step_results) == 2
    first_conversation_id = result.step_results[0].conversation_id
    second_conversation_id = result.step_results[1].conversation_id
    assert first_conversation_id == second_conversation_id
    assert result.conversation_id == first_conversation_id


def test_no_internal_identifiers_helper_flags_unsafe_text() -> None:
    unsafe_reply = (
        "Your hold_id is 550e8400-e29b-41d4-a716-446655440000 and "
        "patient_resolution_id failed in Redis database backend error."
    )
    safe_reply = "Dr. Emily Carter has openings on 2026-07-02."

    unsafe_result = evaluate_no_internal_identifiers(reply=unsafe_reply)
    safe_result = evaluate_no_internal_identifiers(reply=safe_reply)

    assert unsafe_result.passed is False
    assert reply_contains_uuid(unsafe_reply) is True
    assert reply_contains_internal_terms(unsafe_reply) == (
        "patient_resolution_id",
        "hold_id",
        "redis",
        "database",
        "backend error",
    )
    assert safe_result.passed is True
    assert reply_contains_uuid(safe_reply) is False
    assert reply_contains_internal_terms(safe_reply) == ()


def test_no_invented_email_helper() -> None:
    without_user_email = evaluate_no_invented_email(
        reply="Please email support@clinic.example.com for help.",
        user_messages=("Hello, I need an appointment.",),
    )
    assert without_user_email.passed is False

    with_user_email = evaluate_no_invented_email(
        reply="Thanks, we will contact you at jane.doe@example.com.",
        user_messages=("My email is jane.doe@example.com.",),
    )
    assert with_user_email.passed is True
    assert extract_emails("Reach me at Jane.Doe@Example.com") == frozenset(
        {"jane.doe@example.com"},
    )


def test_booking_not_confirmed_early_in_booking_start_scenario(
    availability_guidance_service: ChatReceptionistService,
) -> None:
    scenario = ChatSchedulingEvaluationScenario(
        name="availability_lookup_only",
        description="A date-specific availability request must not confirm booking.",
        steps=(
            ChatSchedulingEvaluationStep(
                user_message="Dr. Emily Carter on 2026-07-02",
                expected_booking_confirmed=False,
                expected_appointment_created=False,
                expected_reply_not_contains_any=("booked", "confirmed your appointment"),
            ),
        ),
    )

    result = run_chat_scheduling_scenario(
        service=availability_guidance_service,
        scenario=scenario,
    )

    assert result.passed is True
    step = result.step_results[0]
    assert step.booking_confirmed is False
    assert step.appointment_id is None


def test_vague_date_scenario_requests_clarification_without_booking(
    availability_guidance_service: ChatReceptionistService,
) -> None:
    scenario = ChatSchedulingEvaluationScenario(
        name="unsupported_next_week",
        description="Vague date requests should ask for clarification, not book.",
        steps=(
            ChatSchedulingEvaluationStep(
                user_message="Dr. Emily Carter availability next week",
                expected_intent=ChatReceptionistIntent.INVALID_DATE,
                expected_booking_confirmed=False,
                expected_appointment_created=False,
                expected_reply_not_contains_any=("booked", "confirmed your appointment"),
                expect_clarification_wording=True,
            ),
        ),
    )

    result = run_chat_scheduling_scenario(
        service=availability_guidance_service,
        scenario=scenario,
    )

    assert result.passed is True
    clarification = next(
        item
        for item in result.step_results[0].expectation_results
        if item.field == "clarification_wording"
    )
    assert clarification.passed is True


def test_llm_failure_does_not_break_chat() -> None:
    service = create_structured_slot_filling_chat_service(
        llm_provider=RaisingLLMProvider(),
    )

    scenario = ChatSchedulingEvaluationScenario(
        name="llm_provider_failure",
        description="Chat should still respond when LLM analysis fails.",
        steps=(
            ChatSchedulingEvaluationStep(
                user_message="Hello there",
                expected_booking_confirmed=False,
                expected_appointment_created=False,
            ),
        ),
    )

    result = run_chat_scheduling_scenario(service=service, scenario=scenario)

    assert result.passed is True
    step = result.step_results[0]
    assert step.reply.strip() != ""
    assert step.booking_confirmed is False
    assert step.appointment_id is None

    direct_result = service.handle_message(ChatMessageInput(message="Hello there"))
    shadow = direct_result.assistant_message.message_metadata["llm_shadow_analysis"]
    assert shadow["used_fallback"] is True
