from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class LLMProviderError(RuntimeError):
    pass


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