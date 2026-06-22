from __future__ import annotations

import pytest

from app.ai.groq_provider import GroqLLMProvider
from app.ai.llm_provider import (
    GroqResponseFormat,
    LLMFinishReason,
    LLMProviderError,
    LLMProviderRateLimitError,
    LLMProviderTimeoutError,
    provider_failure_reason,
)
from app.ai.llm_reliability import LLMFailureReason, failure_reason_for_typed_provider_error
from app.ai.receptionist_output import build_receptionist_analysis_openai_json_schema
from tests.llm_provider_test_helpers import (
    build_receptionist_analysis_payload,
    build_sample_llm_request,
)


class StubGroqHttpClient:
    def __init__(
        self,
        *,
        response: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_payload: dict[str, object] | None = None
        self.last_timeout_seconds: int | None = None

    def post_chat_completion(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        self.last_url = url
        self.last_headers = headers
        self.last_payload = payload
        self.last_timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def build_groq_provider(
    *,
    response: dict[str, object] | None = None,
    error: Exception | None = None,
    response_format: GroqResponseFormat = GroqResponseFormat.JSON_SCHEMA,
) -> tuple[GroqLLMProvider, StubGroqHttpClient]:
    client = StubGroqHttpClient(response=response, error=error)
    provider = GroqLLMProvider(
        api_key="gsk_test",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        timeout_seconds=10,
        temperature=0.0,
        max_output_tokens=800,
        response_format=response_format,
        http_client=client,
    )
    return provider, client


def build_valid_chat_completion_response(*, content: str) -> dict[str, object]:
    return {
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            },
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "total_tokens": 165,
        },
    }


def test_groq_adapter_maps_successful_provider_text_to_llm_response() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, _client = build_groq_provider(
        response=build_valid_chat_completion_response(content=payload),
    )

    response = provider.complete(build_sample_llm_request())

    assert response.content == payload
    assert response.model == "llama-3.3-70b-versatile"
    assert response.finish_reason == LLMFinishReason.STOP
    assert response.estimated_cost_micros == 0
    assert response.provider_metadata == {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "latency_ms": response.provider_metadata["latency_ms"],
        "response_format": "json_schema",
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
    }
    assert isinstance(response.provider_metadata["latency_ms"], int)


def test_groq_adapter_maps_usage_tokens_when_present() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, _client = build_groq_provider(
        response=build_valid_chat_completion_response(content=payload),
    )

    response = provider.complete(build_sample_llm_request())

    assert response.input_tokens == 120
    assert response.output_tokens == 45
    assert response.provider_metadata["total_tokens"] == 165


def test_groq_adapter_handles_missing_usage_safely() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    response_body = build_valid_chat_completion_response(content=payload)
    del response_body["usage"]
    provider, _client = build_groq_provider(response=response_body)

    response = provider.complete(build_sample_llm_request())

    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert "total_tokens" not in response.provider_metadata


def test_groq_timeout_maps_to_provider_timeout_error() -> None:
    provider, _client = build_groq_provider(error=LLMProviderTimeoutError("Groq request timed out"))

    with pytest.raises(LLMProviderTimeoutError, match="timed out"):
        provider.complete(build_sample_llm_request())

    assert (
        provider_failure_reason(LLMProviderTimeoutError("Groq request timed out"))
        == LLMFailureReason.PROVIDER_TIMEOUT
    )
    assert (
        failure_reason_for_typed_provider_error(LLMProviderTimeoutError("timed out"))
        == LLMFailureReason.PROVIDER_TIMEOUT
    )


def test_groq_429_maps_to_rate_limit_error() -> None:
    provider, _client = build_groq_provider(
        error=LLMProviderRateLimitError("Groq rate limit exceeded"),
    )

    with pytest.raises(LLMProviderRateLimitError, match="rate limit"):
        provider.complete(build_sample_llm_request())

    assert (
        provider_failure_reason(LLMProviderRateLimitError("Groq rate limit exceeded"))
        == LLMFailureReason.PROVIDER_RATE_LIMITED
    )


def test_groq_5xx_maps_to_provider_error() -> None:
    provider, _client = build_groq_provider(error=LLMProviderError("Groq server error: 503"))

    with pytest.raises(LLMProviderError, match="503"):
        provider.complete(build_sample_llm_request())


def test_groq_empty_response_raises_empty_response_failure_path() -> None:
    provider, _client = build_groq_provider(
        response=build_valid_chat_completion_response(content="   "),
    )

    with pytest.raises(LLMProviderError, match="did not include text content"):
        provider.complete(build_sample_llm_request())

    assert (
        provider_failure_reason(LLMProviderError("Groq response did not include text content"))
        == LLMFailureReason.EMPTY_RESPONSE
    )


def test_groq_sends_json_schema_response_format_when_configured() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, client = build_groq_provider(
        response=build_valid_chat_completion_response(content=payload),
        response_format=GroqResponseFormat.JSON_SCHEMA,
    )

    provider.complete(build_sample_llm_request())

    assert client.last_payload is not None
    assert client.last_payload["response_format"] == {
        "type": "json_schema",
        "json_schema": build_receptionist_analysis_openai_json_schema(),
    }


def test_groq_sends_json_object_response_format_when_configured() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, client = build_groq_provider(
        response=build_valid_chat_completion_response(content=payload),
        response_format=GroqResponseFormat.JSON_OBJECT,
    )

    provider.complete(build_sample_llm_request())

    assert client.last_payload is not None
    assert client.last_payload["response_format"] == {"type": "json_object"}
    assert client.last_payload["model"] == "llama-3.3-70b-versatile"
    assert client.last_payload["max_tokens"] == 800
    assert client.last_payload["temperature"] == 0.0
    assert client.last_url == "https://api.groq.com/openai/v1/chat/completions"
    assert client.last_headers is not None
    assert client.last_headers["Authorization"] == "Bearer gsk_test"
    assert "gsk_test" not in str(client.last_payload)


def test_groq_stub_client_avoids_real_network_calls() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, client = build_groq_provider(
        response=build_valid_chat_completion_response(content=payload),
    )

    provider.complete(build_sample_llm_request())

    assert client.last_url is not None
    assert client.last_timeout_seconds == 10
