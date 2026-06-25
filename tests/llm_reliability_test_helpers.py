from __future__ import annotations

from app.ai.llm_provider import LLMProviderError, LLMProviderName, LLMRequest, LLMResponse
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
    ReceptionistAnalysisResult,
)

RESULT_RELIABILITY_METADATA_FIELDS = (
    "provider",
    "primary_provider",
    "fallback_provider",
    "used_fallback_provider",
    "attempt_count",
    "primary_attempt_count",
    "fallback_attempt_count",
    "used_repair",
    "used_fallback",
    "failure_category",
    "failure_reason",
    "prompt_version",
)

SHADOW_RELIABILITY_METADATA_FIELDS = (
    "provider",
    "primary_provider",
    "fallback_provider",
    "used_fallback_provider",
    "attempt_count",
    "primary_attempt_count",
    "fallback_attempt_count",
    "used_repair",
    "used_fallback",
    "failure_category",
    "failure_reason",
    "prompt_version",
)


class CountingLLMProvider:
    def __init__(self, content: str, *, name: str = "counting") -> None:
        self.content = content
        self.name = name
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            content=self.content,
            model=f"{self.name}-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class FailOnceThenSucceedProvider:
    def __init__(self, *, success_content: str) -> None:
        self.success_content = success_content
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderError("simulated provider failure")
        return LLMResponse(
            content=self.success_content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class FailOnceThenSucceedCapturingProvider:
    def __init__(self, *, success_content: str) -> None:
        self.success_content = success_content
        self.call_count = 0
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderError("simulated provider failure")
        return LLMResponse(
            content=self.success_content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class SequentialContentProvider:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        index = min(self.call_count, len(self.contents) - 1)
        content = self.contents[index]
        self.call_count += 1
        return LLMResponse(
            content=content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class SequentialCapturingContentProvider:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.call_count = 0
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(self.call_count, len(self.contents) - 1)
        content = self.contents[index]
        self.call_count += 1
        return LLMResponse(
            content=content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class AlwaysFailingLLMProvider:
    def __init__(self, *, message: str = "simulated provider failure") -> None:
        self.message = message
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        raise LLMProviderError(self.message)


def analysis_request() -> ReceptionistAnalysisRequest:
    return ReceptionistAnalysisRequest(
        user_message="Hello",
        conversation_context={},
    )


def build_orchestration_service(
    *,
    primary_provider: CountingLLMProvider
    | FailOnceThenSucceedProvider
    | FailOnceThenSucceedCapturingProvider
    | SequentialContentProvider
    | SequentialCapturingContentProvider
    | AlwaysFailingLLMProvider,
    fallback_provider: CountingLLMProvider | AlwaysFailingLLMProvider | None = None,
    max_primary_attempts: int = 2,
    max_fallback_attempts: int = 0,
    primary_provider_name: LLMProviderName = LLMProviderName.FAKE,
    fallback_provider_name: LLMProviderName | None = None,
) -> LLMReceptionistAnalysisService:
    return LLMReceptionistAnalysisService(
        primary_provider=primary_provider,
        fallback_provider=fallback_provider,
        primary_provider_name=primary_provider_name,
        fallback_provider_name=fallback_provider_name,
        max_primary_attempts=max_primary_attempts,
        max_fallback_attempts=max_fallback_attempts,
    )


def expected_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def assert_result_reliability_metadata(result: ReceptionistAnalysisResult) -> None:
    for field_name in RESULT_RELIABILITY_METADATA_FIELDS:
        assert hasattr(result, field_name), field_name

    assert result.prompt_version == expected_prompt_version()
    assert result.failure_category.value
    assert result.failure_reason.value
    assert result.primary_provider == LLMProviderName.FAKE.value
    assert result.attempt_count == result.primary_attempt_count + result.fallback_attempt_count


def assert_shadow_reliability_metadata(shadow: dict[str, object]) -> None:
    for field_name in SHADOW_RELIABILITY_METADATA_FIELDS:
        assert field_name in shadow, field_name

    assert shadow["prompt_version"] == expected_prompt_version()


REPAIR_PROMPT_MARKER = (
    "Previous output could not be parsed or failed schema validation"
)


def request_includes_repair_prompt(request: LLMRequest) -> bool:
    return any(REPAIR_PROMPT_MARKER in message.content for message in request.messages)


def last_user_message(request: LLMRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return ""
