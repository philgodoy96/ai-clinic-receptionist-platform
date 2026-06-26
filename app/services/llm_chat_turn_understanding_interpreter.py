from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.ai.chat_turn_understanding_prompt import build_chat_turn_understanding_system_prompt
from app.ai.llm_provider import (
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    provider_failure_reason,
)
from app.ai.llm_reliability import (
    LLMFailureReason,
    is_fallback_provider_eligible,
    is_primary_provider_retryable,
    is_structural_output_failure,
    parse_failure_reason_from_parse_error,
)
from app.ai.prompt_versions import get_current_chat_turn_understanding_prompt_metadata
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output_with_repair_flag,
)
from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
)

logger = logging.getLogger("app.llm_chat_turn_understanding")

_CONTEXT_SNAPSHOT_PREFIX = (
    "Backend-provided chat turn understanding context. "
    "Use this only to interpret the user's latest message. "
    "Do not invent missing details. "
    "Do not assume a durable action has happened unless explicitly stated."
)
_STRUCTURAL_OUTPUT_REPAIR_INSTRUCTION = (
    "Previous output could not be parsed or failed schema validation. "
    "Return only a valid JSON object matching the ChatTurnUnderstandingResult schema. "
    "Do not include markdown, code fences, comments, explanations, or extra fields. "
    "If the user's message is ambiguous, represent that through the allowed schema "
    "fields rather than inventing information."
)
_FALLBACK_CLARIFICATION_QUESTION = (
    "Could you please clarify what you'd like to do?"
)


def fallback_chat_turn_understanding_result(
    *,
    reason: str = "LLM chat turn understanding failed",
) -> ChatTurnUnderstandingResult:
    return ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.FALLBACK,
        confidence=0.0,
        reason=reason,
        clarification_question=_FALLBACK_CLARIFICATION_QUESTION,
    )


@dataclass(frozen=True, slots=True)
class _ProviderAttemptSuccess:
    response: LLMResponse
    result: ChatTurnUnderstandingResult
    used_repair: bool


@dataclass(frozen=True, slots=True)
class _ProviderAttemptFailure:
    failure_reason: LLMFailureReason
    error: str
    used_repair: bool
    retryable: bool


class LLMChatTurnUnderstandingInterpreter:
    def __init__(
        self,
        *,
        primary_provider: LLMProvider,
        fallback_provider: LLMProvider | None = None,
        max_primary_attempts: int = 2,
        max_fallback_attempts: int = 1,
    ) -> None:
        if max_primary_attempts < 1:
            raise ValueError("max_primary_attempts must be >= 1")
        if max_fallback_attempts < 0:
            raise ValueError("max_fallback_attempts must be >= 0")

        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.max_primary_attempts = max_primary_attempts
        self.max_fallback_attempts = max_fallback_attempts

    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        started_at = perf_counter()
        prompt_version = get_current_chat_turn_understanding_prompt_metadata().version

        primary_attempt_count = 0
        fallback_attempt_count = 0
        used_repair = False
        include_repair_prompt = False
        last_error = "LLM chat turn understanding failed"
        last_failure_reason = LLMFailureReason.PROVIDER_EXCEPTION

        while primary_attempt_count < self.max_primary_attempts:
            primary_attempt_count += 1
            llm_request = self._build_llm_request(
                request=request,
                prompt_version=prompt_version,
                include_repair_prompt=include_repair_prompt,
            )
            attempt = self._attempt_provider(
                provider=self.primary_provider,
                llm_request=llm_request,
                used_repair=used_repair,
            )
            used_repair = attempt.used_repair
            if isinstance(attempt, _ProviderAttemptSuccess):
                self._log_success(
                    started_at=started_at,
                    prompt_version=prompt_version,
                    primary_attempt_count=primary_attempt_count,
                    fallback_attempt_count=fallback_attempt_count,
                    used_repair=used_repair,
                )
                return attempt.result

            last_error = attempt.error
            last_failure_reason = attempt.failure_reason
            if not attempt.retryable:
                break

            if is_structural_output_failure(last_failure_reason):
                include_repair_prompt = True

        if (
            self.fallback_provider is not None
            and self.max_fallback_attempts > 0
            and is_fallback_provider_eligible(last_failure_reason)
        ):
            while fallback_attempt_count < self.max_fallback_attempts:
                fallback_attempt_count += 1
                llm_request = self._build_llm_request(
                    request=request,
                    prompt_version=prompt_version,
                    include_repair_prompt=include_repair_prompt,
                )
                attempt = self._attempt_provider(
                    provider=self.fallback_provider,
                    llm_request=llm_request,
                    used_repair=used_repair,
                )
                used_repair = attempt.used_repair
                if isinstance(attempt, _ProviderAttemptSuccess):
                    self._log_success(
                        started_at=started_at,
                        prompt_version=prompt_version,
                        primary_attempt_count=primary_attempt_count,
                        fallback_attempt_count=fallback_attempt_count,
                        used_repair=used_repair,
                        used_fallback_provider=True,
                    )
                    return attempt.result

                last_error = attempt.error
                last_failure_reason = attempt.failure_reason
                if not is_fallback_provider_eligible(last_failure_reason):
                    break

                if is_structural_output_failure(last_failure_reason):
                    include_repair_prompt = True

        self._log_fallback(
            started_at=started_at,
            prompt_version=prompt_version,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=fallback_attempt_count,
            error=last_error,
        )
        return fallback_chat_turn_understanding_result(reason=last_error)

    def _attempt_provider(
        self,
        *,
        provider: LLMProvider,
        llm_request: LLMRequest,
        used_repair: bool,
    ) -> _ProviderAttemptSuccess | _ProviderAttemptFailure:
        try:
            response = provider.complete(llm_request)
            parse_outcome = parse_structured_output_with_repair_flag(
                raw_output=response.content,
                model_type=ChatTurnUnderstandingResult,
            )
            updated_used_repair = used_repair or parse_outcome.used_repair
            return _ProviderAttemptSuccess(
                response=response,
                result=parse_outcome.value,
                used_repair=updated_used_repair,
            )
        except LLMProviderError as exc:
            failure_reason = provider_failure_reason(exc)
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except StructuredOutputParseError as exc:
            failure_reason = parse_failure_reason_from_parse_error(
                repair_attempted=exc.repair_attempted,
            )
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except StructuredOutputValidationError as exc:
            failure_reason = LLMFailureReason.SCHEMA_VALIDATION_FAILED
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except Exception as exc:
            logger.exception(
                "llm_chat_turn_understanding_unknown_error",
                extra={"event": "llm_chat_turn_understanding_unknown_error"},
            )
            failure_reason = LLMFailureReason.PROVIDER_EXCEPTION
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=False,
            )

    def _build_llm_request(
        self,
        *,
        request: ChatTurnUnderstandingRequest,
        prompt_version: str,
        include_repair_prompt: bool = False,
    ) -> LLMRequest:
        messages = [
            LLMMessage(
                role="system",
                content=build_chat_turn_understanding_system_prompt(),
            ),
        ]

        context_snapshot = build_chat_turn_understanding_context_snapshot(request)
        if context_snapshot:
            snapshot_json = json.dumps(
                context_snapshot,
                separators=(",", ":"),
                sort_keys=True,
            )
            messages.append(
                LLMMessage(
                    role="system",
                    content=(
                        f"{_CONTEXT_SNAPSHOT_PREFIX}\n"
                        f"Chat turn understanding context:\n{snapshot_json}"
                    ),
                ),
            )

        if include_repair_prompt:
            messages.append(
                LLMMessage(
                    role="system",
                    content=_STRUCTURAL_OUTPUT_REPAIR_INSTRUCTION,
                ),
            )

        messages.append(
            LLMMessage(
                role="user",
                content=request.latest_user_message,
            ),
        )

        return LLMRequest(
            messages=messages,
            response_format="json",
            temperature=0.0,
            metadata={
                "component": "chat_turn_understanding",
                "prompt_version": prompt_version,
            },
        )

    def _log_success(
        self,
        *,
        started_at: float,
        prompt_version: str,
        primary_attempt_count: int,
        fallback_attempt_count: int,
        used_repair: bool,
        used_fallback_provider: bool = False,
    ) -> None:
        logger.info(
            "llm_chat_turn_understanding_success",
            extra={
                "event": "llm_chat_turn_understanding_success",
                "prompt_version": prompt_version,
                "latency_ms": self._elapsed_ms(started_at),
                "primary_attempt_count": primary_attempt_count,
                "fallback_attempt_count": fallback_attempt_count,
                "used_repair": used_repair,
                "used_fallback_provider": used_fallback_provider,
            },
        )

    def _log_fallback(
        self,
        *,
        started_at: float,
        prompt_version: str,
        primary_attempt_count: int,
        fallback_attempt_count: int,
        error: str,
    ) -> None:
        logger.warning(
            "llm_chat_turn_understanding_fallback",
            extra={
                "event": "llm_chat_turn_understanding_fallback",
                "prompt_version": prompt_version,
                "latency_ms": self._elapsed_ms(started_at),
                "primary_attempt_count": primary_attempt_count,
                "fallback_attempt_count": fallback_attempt_count,
                "error": error,
            },
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)


def build_chat_turn_understanding_context_snapshot(
    request: ChatTurnUnderstandingRequest,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "conversation_state": request.conversation_state.value,
        "expected_response_type": request.expected_response_type.value,
        "allowed_intents": [intent.value for intent in request.allowed_intents],
    }

    if request.last_assistant_question is not None:
        snapshot["last_assistant_question"] = request.last_assistant_question

    if request.current_context:
        snapshot["current_context"] = request.current_context

    if request.offered_slots:
        snapshot["offered_slots"] = [
            slot.model_dump(mode="json") for slot in request.offered_slots
        ]

    if request.known_specialties:
        snapshot["known_specialties"] = [
            specialty.model_dump(mode="json") for specialty in request.known_specialties
        ]

    if request.known_doctors:
        snapshot["known_doctors"] = [
            doctor.model_dump(mode="json") for doctor in request.known_doctors
        ]

    if request.locale is not None:
        snapshot["locale"] = request.locale

    return snapshot
