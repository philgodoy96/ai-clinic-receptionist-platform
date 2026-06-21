from __future__ import annotations

import json

from app.ai.reliability import LLMFailureReason
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import RaisingLLMProvider, StaticContentLLMProvider


def test_llm_receptionist_analysis_service_records_fallback_on_provider_error() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_ERROR
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
    assert result.failure_reason == LLMFailureReason.SCHEMA_VALIDATION_ERROR


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
