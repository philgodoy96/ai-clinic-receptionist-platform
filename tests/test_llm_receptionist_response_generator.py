from __future__ import annotations

import json

import pytest

from app.ai.llm_provider import (
    LLMProviderName,
    LLMProviderTimeoutError,
    LLMRequest,
)
from app.ai.response_output_validator import ResponseOutputValidationError, ResponseOutputValidator
from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseSafetyLevel,
    ReceptionistResponseType,
)
from app.domain.receptionist.response_planning import ResponsePlan, build_response_plan
from app.services.receptionist_response_generator import (
    MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH,
    DeterministicReceptionistResponseGenerator,
    LLMReceptionistResponseGenerator,
)
from tests.llm_provider_test_helpers import RaisingLLMProvider, StaticContentLLMProvider
from tests.llm_reliability_test_helpers import CountingLLMProvider

_SECRET_METADATA_MARKERS = frozenset(
    {
        "api_key",
        "groq_api_key",
        "raw_provider_output",
        "raw_prompt",
        "system_prompt",
    },
)


def _build_llm_generator(
    provider: object,
    *,
    validate_output: bool = True,
) -> LLMReceptionistResponseGenerator:
    return LLMReceptionistResponseGenerator(
        provider=provider,  # type: ignore[arg-type]
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
        validate_output=validate_output,
    )


def _scheduling_plan(**facts: object) -> ResponsePlan:
    return build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help with scheduling.",
        facts={"template_type": "ask_for_specialty", **facts},
    )


def test_mocked_llm_success_returns_llm_mode() -> None:
    provider = StaticContentLLMProvider(
        json.dumps({"text": "Which specialty would you like to book?"}),
    )
    generator = _build_llm_generator(provider)
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.LLM
    assert response.used_fallback is False
    assert response.text == "Which specialty would you like to book?"
    assert response.metadata["provider"] == "fake"
    assert response.metadata["model"] == "test-model"
    assert response.metadata["input_tokens"] == 12
    assert response.metadata["output_tokens"] == 8
    assert response.metadata["prompt_version"] == "receptionist-response-v1"


def test_provider_failure_returns_deterministic_fallback() -> None:
    generator = _build_llm_generator(RaisingLLMProvider())
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "LLMProviderError"
    assert "specialty" in response.text.lower()


def test_provider_timeout_returns_deterministic_fallback() -> None:
    class TimeoutProvider:
        def complete(self, request: LLMRequest) -> object:
            raise LLMProviderTimeoutError("simulated timeout")

    generator = _build_llm_generator(TimeoutProvider())
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "LLMProviderTimeoutError"


def test_malformed_output_returns_deterministic_fallback() -> None:
    generator = _build_llm_generator(StaticContentLLMProvider("not-json"))
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "StructuredOutputParseError"


def test_unsafe_output_returns_deterministic_fallback() -> None:
    provider = StaticContentLLMProvider(
        json.dumps({"text": "Here is your api_key: sk-abcdefghijklmnopqrstuvwxyz"}),
    )
    generator = _build_llm_generator(provider)
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "ResponseOutputValidationError"


def test_emergency_plan_does_not_call_llm() -> None:
    provider = CountingLLMProvider(json.dumps({"text": "ignored"}))
    generator = _build_llm_generator(provider)
    plan = build_response_plan(
        response_type=ReceptionistResponseType.CRITICAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Emergency fallback.",
        facts={"template_type": "emergency_guidance"},
        deterministic_behavior=True,
    )

    response = generator.generate(plan)

    assert provider.call_count == 0
    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert "emergency" in response.text.lower()


def test_critical_safety_plan_uses_deterministic_output_by_default() -> None:
    provider = CountingLLMProvider(json.dumps({"text": "ignored"}))
    generator = _build_llm_generator(provider)
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Safety fallback.",
        facts={"template_type": "ask_for_specialty"},
        safety_level=ReceptionistResponseSafetyLevel.CRITICAL,
    )

    response = generator.generate(plan)

    assert provider.call_count == 0
    assert response.mode == ReceptionistResponseMode.DETERMINISTIC


def test_no_real_provider_calls_in_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai import provider_factory

    def fail_if_called(*args: object, **kwargs: object) -> object:
        msg = "real provider factory must not be called in unit tests"
        raise AssertionError(msg)

    monkeypatch.setattr(provider_factory, "create_llm_provider_from_settings", fail_if_called)
    provider = StaticContentLLMProvider(json.dumps({"text": "Hello from mocked provider."}))
    generator = _build_llm_generator(provider)
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.LLM
    assert response.text == "Hello from mocked provider."


def test_metadata_contains_provider_model_tokens_without_secrets() -> None:
    provider = StaticContentLLMProvider(
        json.dumps({"text": "Please share your preferred specialty."}),
    )
    generator = _build_llm_generator(provider)
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.metadata["provider"] == "fake"
    assert response.metadata["model"] == "test-model"
    assert response.metadata["input_tokens"] == 12
    assert response.metadata["output_tokens"] == 8
    assert response.metadata["estimated_cost_micros"] == 42

    for key in response.metadata:
        assert key.lower() not in _SECRET_METADATA_MARKERS
    for value in response.metadata.values():
        normalized = str(value).lower()
        for marker in _SECRET_METADATA_MARKERS:
            assert marker not in normalized


def test_output_is_bounded_and_non_empty() -> None:
    provider = StaticContentLLMProvider(
        json.dumps({"text": "Please tell me which specialty you need."}),
    )
    generator = _build_llm_generator(provider)
    plan = _scheduling_plan()

    response = generator.generate(plan)

    assert response.text
    assert len(response.text) <= MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH

    oversized = "x" * (MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH + 50)
    validator = ResponseOutputValidator()
    with pytest.raises(ResponseOutputValidationError):
        validator.validate(text=oversized, plan=plan)
