from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.llm_provider import LLMFinishReason, LLMMessage, LLMProviderError, LLMRequest
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)


class StubBedrockClient:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.last_kwargs: dict[str, Any] | None = None

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def build_provider(
    *,
    response: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> tuple[BedrockLLMProvider, StubBedrockClient]:
    client = StubBedrockClient(response=response, error=error)
    provider = BedrockLLMProvider(
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        region_name="us-east-1",
        timeout_seconds=10,
        max_retries=0,
        temperature=0.0,
        max_tokens=800,
        client=client,
    )
    return provider, client


def build_sample_request() -> LLMRequest:
    return LLMRequest(
        messages=[
            LLMMessage(role="system", content="Additional service context."),
            LLMMessage(role="user", content="Hello, I need an appointment."),
        ],
        response_format="json",
        temperature=0.0,
        max_tokens=800,
    )


def build_valid_converse_response(*, content: str) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": content}],
            },
        },
        "usage": {
            "inputTokens": 120,
            "outputTokens": 45,
        },
        "stopReason": "end_turn",
    }


def test_complete_sends_converse_request_to_client() -> None:
    payload = json.dumps(
        {
            "intent": "greeting",
            "confidence": 0.9,
            "urgency": "normal",
            "extracted": {},
            "requires_human": False,
            "safety_flags": [],
        },
    )
    provider, client = build_provider(response=build_valid_converse_response(content=payload))

    provider.complete(build_sample_request())

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


def test_complete_returns_llm_response_with_content() -> None:
    payload = json.dumps(
        {
            "intent": "greeting",
            "confidence": 0.9,
            "urgency": "normal",
            "extracted": {},
            "requires_human": False,
            "safety_flags": [],
        },
    )
    provider, _client = build_provider(response=build_valid_converse_response(content=payload))

    response = provider.complete(build_sample_request())

    assert response.content == payload
    assert response.model == "anthropic.claude-3-haiku-20240307-v1:0"
    assert response.finish_reason == LLMFinishReason.STOP
    assert response.estimated_cost_micros == 0


def test_complete_maps_token_usage_when_present() -> None:
    payload = json.dumps(
        {
            "intent": "greeting",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    provider, _client = build_provider(response=build_valid_converse_response(content=payload))

    response = provider.complete(build_sample_request())

    assert response.input_tokens == 120
    assert response.output_tokens == 45


def test_complete_uses_zero_tokens_when_usage_missing() -> None:
    payload = json.dumps(
        {
            "intent": "greeting",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    response_body = build_valid_converse_response(content=payload)
    del response_body["usage"]
    provider, _client = build_provider(response=response_body)

    response = provider.complete(build_sample_request())

    assert response.input_tokens == 0
    assert response.output_tokens == 0


def test_malformed_provider_response_raises_provider_error() -> None:
    provider, _client = build_provider(response={"unexpected": "shape"})

    with pytest.raises(LLMProviderError, match="Bedrock response"):
        provider.complete(build_sample_request())


def test_client_exception_raises_provider_error() -> None:
    class FakeClientError(Exception):
        pass

    FakeClientError.__module__ = "botocore.exceptions"

    provider, _client = build_provider(error=FakeClientError("bedrock unavailable"))

    with pytest.raises(LLMProviderError, match="bedrock unavailable"):
        provider.complete(build_sample_request())


def test_bedrock_provider_error_triggers_service_fallback() -> None:
    class FakeClientError(Exception):
        pass

    FakeClientError.__module__ = "botocore.exceptions"
    provider, _client = build_provider(error=FakeClientError("bedrock unavailable"))
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.error == "bedrock unavailable"
