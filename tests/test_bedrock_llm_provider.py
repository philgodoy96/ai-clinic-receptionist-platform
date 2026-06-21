from __future__ import annotations

import pytest

from app.ai.llm_provider import LLMFinishReason
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import (
    build_bedrock_provider,
    build_receptionist_analysis_payload,
    build_sample_llm_request,
    build_valid_converse_response,
    make_botocore_error,
)


def test_bedrock_adapter_maps_successful_provider_text_to_llm_response() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )

    response = provider.complete(build_sample_llm_request())

    assert response.content == payload
    assert response.model == "anthropic.claude-3-haiku-20240307-v1:0"
    assert response.finish_reason == LLMFinishReason.STOP
    assert response.estimated_cost_micros == 0


def test_bedrock_adapter_maps_usage_tokens_when_present() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )

    response = provider.complete(build_sample_llm_request())

    assert response.input_tokens == 120
    assert response.output_tokens == 45


def test_bedrock_adapter_handles_missing_usage_safely() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    response_body = build_valid_converse_response(content=payload)
    del response_body["usage"]
    provider, _client = build_bedrock_provider(response=response_body)

    response = provider.complete(build_sample_llm_request())

    assert response.input_tokens == 0
    assert response.output_tokens == 0


def test_bedrock_adapter_raises_provider_error_for_malformed_response_shape() -> None:
    from app.ai.llm_provider import LLMProviderError

    provider, _client = build_bedrock_provider(response={"unexpected": "shape"})

    with pytest.raises(LLMProviderError, match="Bedrock response"):
        provider.complete(build_sample_llm_request())


def test_bedrock_adapter_sends_converse_request_to_mocked_client() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )

    provider.complete(build_sample_llm_request())

    assert client.last_kwargs is not None
    assert client.last_kwargs["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert client.last_kwargs["inferenceConfig"] == {
        "maxTokens": 800,
        "temperature": 0.0,
    }
    system_texts = [block["text"] for block in client.last_kwargs["system"]]
    assert build_receptionist_system_prompt() in system_texts
    assert "Additional service context." in system_texts
    assert client.last_kwargs["messages"] == [
        {
            "role": "user",
            "content": [{"text": "Hello, I need an appointment."}],
        },
    ]


def test_bedrock_client_exception_raises_provider_error() -> None:
    from app.ai.llm_provider import LLMProviderError

    provider, _client = build_bedrock_provider(
        error=make_botocore_error("bedrock unavailable"),
    )

    with pytest.raises(LLMProviderError, match="bedrock unavailable"):
        provider.complete(build_sample_llm_request())


def test_bedrock_provider_error_triggers_service_fallback() -> None:
    provider, _client = build_bedrock_provider(
        error=make_botocore_error("bedrock unavailable"),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.error == "bedrock unavailable"
