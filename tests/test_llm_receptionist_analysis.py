from __future__ import annotations

import json

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMRequest, LLMResponse
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.reliability import LLMFailureReason
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import (
    RaisingLLMProvider,
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)


def expected_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


class CapturingLLMProvider(StaticContentLLMProvider):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.last_request: LLMRequest | None = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return super().complete(request)


def test_successful_llm_analysis_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.prompt_version == expected_prompt_version()


def test_fallback_llm_analysis_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.prompt_version == expected_prompt_version()


def test_llm_analysis_request_metadata_includes_prompt_version() -> None:
    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)

    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert provider.last_request is not None
    assert provider.last_request.metadata["prompt_version"] == expected_prompt_version()


def test_invalid_json_fallback_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.prompt_version == expected_prompt_version()


def test_safety_violation_fallback_includes_prompt_version() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    service = LLMReceptionistAnalysisService(provider=StaticContentLLMProvider(payload))

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert result.prompt_version == expected_prompt_version()


def test_fake_llm_provider_success_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=FakeLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.prompt_version == expected_prompt_version()


def test_llm_receptionist_analysis_service_records_fallback_on_provider_error() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_EXCEPTION
    assert result.prompt_version == expected_prompt_version()
    assert result.latency_ms >= 0
    assert result.attempt_count == 1


def test_llm_receptionist_analysis_service_records_schema_validation_failure() -> None:
    invalid_payload = json.dumps(
        {
            "intent": "made_up_intent",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(invalid_payload),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED
    assert result.prompt_version == expected_prompt_version()


def test_llm_receptionist_analysis_service_records_low_confidence() -> None:
    low_confidence_payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(low_confidence_payload),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE
    assert result.prompt_version == expected_prompt_version()
