from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.ai.llm_reliability import LLMFailureReason


class LLMProviderError(RuntimeError):
    pass


def provider_failure_reason(error: LLMProviderError) -> LLMFailureReason:
    from app.ai.llm_reliability import classify_provider_error

    return classify_provider_error(str(error))


class LLMProviderName(StrEnum):
    FAKE = "fake"
    BEDROCK = "bedrock"


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


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError