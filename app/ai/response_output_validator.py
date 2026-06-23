from __future__ import annotations

import re

from app.domain.receptionist.response_planning import ResponsePlan

MAX_RESPONSE_OUTPUT_TEXT_LENGTH = 2000

_BLOCKED_OUTPUT_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9]{8,}", re.IGNORECASE),
    re.compile(r"\bapi[_-]?key\b", re.IGNORECASE),
    re.compile(r"\b(raw_provider_output|raw_prompt|system_prompt)\b", re.IGNORECASE),
)


class ResponseOutputValidationError(ValueError):
    """Raised when generated response text fails safety or shape validation."""


class ResponseOutputValidator:
    def __init__(
        self,
        *,
        max_length: int = MAX_RESPONSE_OUTPUT_TEXT_LENGTH,
    ) -> None:
        if max_length < 1:
            msg = "max_length must be >= 1"
            raise ValueError(msg)
        self.max_length = max_length

    def validate(self, *, text: str, plan: ResponsePlan) -> str:
        normalized = text.strip()
        if not normalized:
            raise ResponseOutputValidationError("text is required")

        if len(normalized) > self.max_length:
            raise ResponseOutputValidationError("text exceeds max length")

        for pattern in _BLOCKED_OUTPUT_PATTERNS:
            if pattern.search(normalized):
                raise ResponseOutputValidationError("unsafe output pattern detected")

        if plan.fallback_text.strip() and not normalized:
            raise ResponseOutputValidationError("text is required")

        return normalized
