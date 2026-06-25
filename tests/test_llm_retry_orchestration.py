from __future__ import annotations

import json

from app.ai.llm_provider import LLMProviderError, LLMProviderName, LLMProviderRateLimitError
from app.ai.llm_reliability import LLMFailureReason
from app.ai.receptionist_output import ReceptionistLLMIntent
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import build_receptionist_analysis_payload
from tests.llm_reliability_test_helpers import (
    CountingLLMProvider,
    FailOnceThenSucceedCapturingProvider,
    FailOnceThenSucceedProvider,
    SequentialCapturingContentProvider,
    SequentialContentProvider,
    analysis_request,
    assert_result_reliability_metadata,
    build_orchestration_service,
    last_user_message,
    request_includes_repair_prompt,
)
from tests.test_groq_llm_provider import (
    SequentialStubGroqHttpClient,
    build_groq_provider_with_client,
    build_valid_chat_completion_response,
)


def test_first_attempt_success_calls_provider_once_without_fallback() -> None:
    fallback = CountingLLMProvider(
        build_receptionist_analysis_payload(),
        name="fallback",
    )
    primary = CountingLLMProvider(build_receptionist_analysis_payload(), name="primary")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.attempt_count == 1
    assert result.used_fallback is False
    assert result.used_fallback_provider is False
    assert_result_reliability_metadata(result)


def test_local_repair_from_markdown_fenced_json_without_retry() -> None:
    payload = build_receptionist_analysis_payload()
    fenced_content = f"```json\n{payload}\n```"
    primary = CountingLLMProvider(fenced_content)
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert result.used_repair is True
    assert result.used_fallback is False
    assert_result_reliability_metadata(result)


def test_local_repair_from_extra_wrapped_text_without_retry() -> None:
    payload = build_receptionist_analysis_payload()
    wrapped_content = f"Here is the structured result:\n{payload}\nEnd of output."
    primary = CountingLLMProvider(wrapped_content)
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert result.used_repair is True
    assert result.primary_attempt_count == 1
    assert_result_reliability_metadata(result)


def test_provider_exception_retries_and_succeeds_on_second_attempt() -> None:
    primary = FailOnceThenSucceedProvider(
        success_content=build_receptionist_analysis_payload(),
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert result.attempt_count == 2
    assert_result_reliability_metadata(result)


def test_invalid_json_retries_once_when_retryable_then_succeeds() -> None:
    primary = SequentialContentProvider(
        [
            "this is not valid json",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert_result_reliability_metadata(result)


def test_json_repair_failure_retries_once_when_retryable_then_succeeds() -> None:
    primary = SequentialContentProvider(
        [
            "{not valid json}",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert_result_reliability_metadata(result)


def test_invalid_json_after_max_attempts_uses_deterministic_fallback() -> None:
    primary = SequentialContentProvider(
        [
            "this is not valid json",
            "still not valid json",
        ],
    )
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.primary_attempt_count == 2
    assert result.fallback_attempt_count == 0
    assert_result_reliability_metadata(result)


def test_safety_violation_does_not_retry_primary_or_call_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert_result_reliability_metadata(result)


def test_medical_emergency_succeeds_without_retry_or_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="emergency",
        urgency="emergency",
        confidence=0.99,
        safety_flags=["medical_emergency"],
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="I have chest pain",
            conversation_context={},
        ),
    )

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.used_fallback_provider is False
    assert result.analysis.intent == ReceptionistLLMIntent.EMERGENCY
    assert_result_reliability_metadata(result)


def test_explicit_human_request_succeeds_without_retry_or_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="human_escalation_request",
        requires_human=True,
        confidence=0.9,
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Can I speak to a real person?",
            conversation_context={},
        ),
    )

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.analysis.intent == ReceptionistLLMIntent.HUMAN_ESCALATION_REQUEST
    assert_result_reliability_metadata(result)


def test_low_confidence_does_not_retry_or_call_fallback() -> None:
    payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE
    assert_result_reliability_metadata(result)


def test_success_metadata_includes_all_reliability_fields() -> None:
    service = build_orchestration_service(
        primary_provider=CountingLLMProvider(build_receptionist_analysis_payload()),
    )

    result = service.analyze_message(analysis_request())

    assert result.attempt_count == 1
    assert result.primary_attempt_count == 1
    assert result.fallback_attempt_count == 0
    assert result.failure_category.value == "none"
    assert result.failure_reason == LLMFailureReason.NONE
    assert_result_reliability_metadata(result)


def _build_groq_orchestration_service(
    client: SequentialStubGroqHttpClient,
    *,
    fallback_provider: CountingLLMProvider | None = None,
    max_primary_attempts: int = 2,
    max_fallback_attempts: int = 0,
    fallback_provider_name: LLMProviderName | None = None,
) -> tuple[LLMReceptionistAnalysisService, SequentialStubGroqHttpClient]:
    service = LLMReceptionistAnalysisService(
        primary_provider=build_groq_provider_with_client(client),
        fallback_provider=fallback_provider,
        primary_provider_name=LLMProviderName.GROQ,
        fallback_provider_name=fallback_provider_name,
        max_primary_attempts=max_primary_attempts,
        max_fallback_attempts=max_fallback_attempts,
    )
    return service, client


def test_groq_provider_exception_retries_via_orchestration_not_internally() -> None:
    payload = build_receptionist_analysis_payload()
    client = SequentialStubGroqHttpClient(
        steps=[
            (None, LLMProviderError("Groq server error: 503")),
            (build_valid_chat_completion_response(content=payload), None),
        ],
    )
    service, client = _build_groq_orchestration_service(client)

    result = service.analyze_message(analysis_request())

    assert client.call_count == 2
    assert result.primary_attempt_count == 2
    assert result.used_fallback is False
    assert result.provider == LLMProviderName.GROQ.value
    assert result.primary_provider == LLMProviderName.GROQ.value


def test_groq_rate_limit_is_retried_by_orchestration() -> None:
    payload = build_receptionist_analysis_payload()
    client = SequentialStubGroqHttpClient(
        steps=[
            (None, LLMProviderRateLimitError("Groq rate limit exceeded")),
            (build_valid_chat_completion_response(content=payload), None),
        ],
    )
    service, client = _build_groq_orchestration_service(client)

    result = service.analyze_message(analysis_request())

    assert client.call_count == 2
    assert result.primary_attempt_count == 2
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.NONE


def test_groq_invalid_json_uses_existing_parse_repair_retry_path() -> None:
    payload = build_receptionist_analysis_payload()
    client = SequentialStubGroqHttpClient(
        steps=[
            (build_valid_chat_completion_response(content="this is not valid json"), None),
            (build_valid_chat_completion_response(content=payload), None),
        ],
    )
    service, client = _build_groq_orchestration_service(client)

    result = service.analyze_message(analysis_request())

    assert client.call_count == 2
    assert result.primary_attempt_count == 2
    assert result.used_fallback is False


def test_groq_safety_violation_does_not_retry_or_call_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    client = SequentialStubGroqHttpClient(
        steps=[
            (build_valid_chat_completion_response(content=payload), None),
        ],
    )
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service, client = _build_groq_orchestration_service(
        client,
        fallback_provider=fallback,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.FAKE,
    )

    result = service.analyze_message(analysis_request())

    assert client.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION


def test_json_parse_failure_uses_repair_prompt_on_retry() -> None:
    user_message = "I need a cardiologist tomorrow morning."
    primary = SequentialCapturingContentProvider(
        [
            "this is not valid json",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message=user_message,
            conversation_context={},
        ),
    )

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert request_includes_repair_prompt(primary.requests[0]) is False
    assert request_includes_repair_prompt(primary.requests[1]) is True
    assert last_user_message(primary.requests[0]) == user_message
    assert last_user_message(primary.requests[1]) == user_message
    assert_result_reliability_metadata(result)


def test_schema_validation_failure_retries_with_repair_prompt_and_succeeds() -> None:
    invalid_payload = json.dumps(
        {
            "intent": "made_up_intent",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    primary = SequentialCapturingContentProvider(
        [
            invalid_payload,
            build_receptionist_analysis_payload(
                intent="appointment_request",
                confidence=0.9,
            ),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.NONE
    assert request_includes_repair_prompt(primary.requests[1]) is True
    assert_result_reliability_metadata(result)


def test_schema_validation_failure_after_max_attempts_uses_deterministic_fallback() -> None:
    invalid_payload = json.dumps(
        {
            "intent": "made_up_intent",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    primary = SequentialCapturingContentProvider([invalid_payload, invalid_payload])
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED
    assert request_includes_repair_prompt(primary.requests[1]) is True
    assert_result_reliability_metadata(result)


def test_context_snapshot_preserved_across_repair_retry_without_pii() -> None:
    user_message = "tomorrow morning"
    context: dict[str, object] = {
        "selected_specialty_name": "Cardiology",
        "selected_doctor_name": "Dr. Emily Carter",
        "hold_id": "hold-uuid-1",
        "patient_email": "real@example.com",
    }
    primary = SequentialCapturingContentProvider(
        [
            "this is not valid json",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message=user_message,
            conversation_context=context,
        ),
    )

    assert result.used_fallback is False
    assert primary.call_count == 2
    for request in primary.requests:
        contents = "\n".join(message.content for message in request.messages)
        assert "Conversation context snapshot:" in contents
        assert '"selected_specialty":"Cardiology"' in contents
        assert last_user_message(request) == user_message
        assert "hold-uuid-1" not in contents
        assert "real@example.com" not in contents
    assert request_includes_repair_prompt(primary.requests[1]) is True


def test_provider_exception_retry_does_not_add_repair_prompt() -> None:
    primary = FailOnceThenSucceedCapturingProvider(
        success_content=build_receptionist_analysis_payload(),
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert all(request_includes_repair_prompt(request) is False for request in primary.requests)
    assert_result_reliability_metadata(result)
