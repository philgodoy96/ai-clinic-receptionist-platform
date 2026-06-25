from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.ai.llm_reliability import LLMFailureReason


class LLMProviderError(RuntimeError):
    pass


class LLMProviderTimeoutError(LLMProviderError):
    pass


class LLMProviderRateLimitError(LLMProviderError):
    pass


def provider_failure_reason(error: LLMProviderError) -> LLMFailureReason:
    from app.ai.llm_reliability import (
        classify_provider_error,
        failure_reason_for_typed_provider_error,
    )

    typed_reason = failure_reason_for_typed_provider_error(error)
    if typed_reason is not None:
        return typed_reason
    return classify_provider_error(str(error))


class LLMProviderName(StrEnum):
    FAKE = "fake"
    BEDROCK = "bedrock"
    GROQ = "groq"


def supported_llm_provider_names() -> str:
    return ", ".join(member.value for member in LLMProviderName)


class GroqResponseFormat(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    NONE = "none"


class LLMFinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: list[LLMMessage]
    response_format: str = "json"
    temperature: float = 0.0
    max_tokens: int = 800
    # Optional observability fields such as component and prompt_version (service-owned).
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_micros: int
    finish_reason: LLMFinishReason = LLMFinishReason.STOP
    provider_metadata: dict[str, str | int] = field(default_factory=dict)


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
