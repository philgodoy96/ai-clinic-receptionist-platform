from __future__ import annotations

import json
from typing import Any

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.llm_provider import LLMMessage, LLMProviderError, LLMRequest, LLMResponse


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


class RaisingLLMProvider:
    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderError("simulated provider failure")


class StaticContentLLMProvider:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content=self.content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


def make_botocore_error(message: str) -> Exception:
    error_type: type[Exception] = type(
        "FakeClientError",
        (Exception,),
        {"__module__": "botocore.exceptions"},
    )
    return error_type(message)


def build_bedrock_provider(
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


def build_sample_llm_request() -> LLMRequest:
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


def build_receptionist_analysis_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "intent": "greeting",
        "confidence": 0.9,
        "urgency": "normal",
        "extracted": {
            "specialty": None,
            "doctor_name": None,
            "date": None,
            "time": None,
            "patient_identity": {
                "full_name": None,
                "date_of_birth": None,
                "phone": None,
                "email": None,
            },
        },
        "requires_human": False,
        "safety_flags": [],
    }
    payload.update(overrides)
    return json.dumps(payload)
