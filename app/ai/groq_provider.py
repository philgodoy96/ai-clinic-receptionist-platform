from __future__ import annotations

import json
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.ai.llm_provider import (
    GroqResponseFormat,
    LLMFinishReason,
    LLMProviderError,
    LLMProviderRateLimitError,
    LLMProviderTimeoutError,
    LLMRequest,
    LLMResponse,
)
from app.ai.receptionist_output import build_receptionist_analysis_openai_json_schema

GROQ_HTTP_USER_AGENT = "ai-clinic-receptionist-platform/1.0"


class GroqHttpClient(Protocol):
    def post_chat_completion(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        raise NotImplementedError


class UrllibGroqHttpClient:
    def post_chat_completion(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace").strip()
            error_suffix = f": {error_body}" if error_body else ""
            if exc.code == 429:
                raise LLMProviderRateLimitError("Groq rate limit exceeded") from exc
            if exc.code >= 500:
                raise LLMProviderError(f"Groq server error: {exc.code}{error_suffix}") from exc
            raise LLMProviderError(f"Groq request failed: {exc.code}{error_suffix}") from exc
        except TimeoutError as exc:
            raise LLMProviderTimeoutError("Groq request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMProviderTimeoutError("Groq request timed out") from exc
            raise LLMProviderError("Groq request failed") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Groq response was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise LLMProviderError("Groq response was not a JSON object")

        return parsed


class GroqLLMProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: int,
        temperature: float,
        max_output_tokens: int,
        response_format: GroqResponseFormat,
        http_client: GroqHttpClient | None = None,
        json_schema_builder: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._default_temperature = temperature
        self._default_max_output_tokens = max_output_tokens
        self._response_format = response_format
        self._json_schema_builder = (
            json_schema_builder or build_receptionist_analysis_openai_json_schema
        )
        self._http_client = http_client if http_client is not None else UrllibGroqHttpClient()

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._build_chat_completion_payload(request)
        started_at = perf_counter()

        try:
            response_body = self._http_client.post_chat_completion(
                url=f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": GROQ_HTTP_USER_AGENT,
                },
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(f"Groq request failed: {exc}") from exc

        latency_ms = int((perf_counter() - started_at) * 1000)

        try:
            content, input_tokens, output_tokens, total_tokens, finish_reason, model = (
                self._parse_chat_completion_response(response_body)
            )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(f"Failed to parse Groq response: {exc}") from exc

        if not content.strip():
            raise LLMProviderError("Groq response did not include text content")

        provider_metadata: dict[str, str | int] = {
            "provider": "groq",
            "model": model,
            "latency_ms": latency_ms,
            "response_format": self._response_format.value,
        }
        if input_tokens is not None:
            provider_metadata["input_tokens"] = input_tokens
        if output_tokens is not None:
            provider_metadata["output_tokens"] = output_tokens
        if total_tokens is not None:
            provider_metadata["total_tokens"] = total_tokens

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            estimated_cost_micros=0,
            finish_reason=finish_reason,
            provider_metadata=provider_metadata,
        )

    def _build_chat_completion_payload(self, request: LLMRequest) -> dict[str, Any]:
        temperature = request.temperature if request.temperature >= 0 else self._default_temperature
        max_tokens = request.max_tokens or self._default_max_output_tokens
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._build_messages(request),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response_format = self._build_response_format()
        if response_format is not None:
            payload["response_format"] = response_format

        return payload

    def _build_messages(self, request: LLMRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for message in request.messages:
            if message.role not in {"system", "user", "assistant"}:
                continue
            if not message.content.strip():
                continue
            messages.append({"role": message.role, "content": message.content})

        if not any(message["role"] == "user" for message in messages):
            raise LLMProviderError("Groq request must include at least one user message")

        return messages

    def _build_response_format(self) -> dict[str, Any] | None:
        if self._response_format == GroqResponseFormat.NONE:
            return None
        if self._response_format == GroqResponseFormat.JSON_OBJECT:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": self._json_schema_builder(),
        }

    def _parse_chat_completion_response(
        self,
        response_body: dict[str, Any],
    ) -> tuple[str, int | None, int | None, int | None, LLMFinishReason, str]:
        choices = response_body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProviderError("Groq response missing choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMProviderError("Groq response choice was not an object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError("Groq response missing assistant message")

        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProviderError("Groq response missing text content")

        input_tokens, output_tokens, total_tokens = self._extract_usage(response_body)
        finish_reason = self._map_finish_reason(first_choice.get("finish_reason"))
        model = response_body.get("model")
        resolved_model = model if isinstance(model, str) and model else self._model

        return content, input_tokens, output_tokens, total_tokens, finish_reason, resolved_model

    def _extract_usage(
        self,
        response_body: dict[str, Any],
    ) -> tuple[int | None, int | None, int | None]:
        usage = response_body.get("usage")
        if not isinstance(usage, dict):
            return None, None, None

        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        return (
            input_tokens if isinstance(input_tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
            total_tokens if isinstance(total_tokens, int) else None,
        )

    def _map_finish_reason(self, finish_reason: Any) -> LLMFinishReason:
        if not isinstance(finish_reason, str):
            return LLMFinishReason.STOP

        normalized = finish_reason.lower()
        if normalized in {"stop", "end_turn"}:
            return LLMFinishReason.STOP
        if normalized in {"length", "max_tokens"}:
            return LLMFinishReason.LENGTH
        if normalized in {"tool_calls", "tool_call"}:
            return LLMFinishReason.TOOL_CALL

        return LLMFinishReason.ERROR
