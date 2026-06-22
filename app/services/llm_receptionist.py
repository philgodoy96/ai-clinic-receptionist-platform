from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

from app.ai.llm_provider import (
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    provider_failure_reason,
)
from app.ai.llm_reliability import (
    MIN_ACCEPTED_CONFIDENCE,
    LLMFailureCategory,
    LLMFailureReason,
    LLMOutputSafetyViolation,
    failure_category_for_reason,
    is_primary_provider_retryable,
    parse_failure_reason_from_parse_error,
)
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.receptionist_output import (
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
    fallback_receptionist_analysis,
)
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output_with_repair_flag,
)

logger = logging.getLogger("app.llm_receptionist")


@dataclass(frozen=True, slots=True)
class ReceptionistAnalysisRequest:
    user_message: str
    conversation_context: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReceptionistAnalysisResult:
    analysis: ReceptionistLLMAnalysis
    model: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_micros: int
    latency_ms: int
    attempt_count: int
    primary_attempt_count: int
    fallback_attempt_count: int
    used_repair: bool
    used_fallback: bool
    failure_reason: LLMFailureReason
    failure_category: LLMFailureCategory
    prompt_version: str
    error: str | None = None


class LLMReceptionistAnalysisService:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        max_primary_attempts: int = 2,
    ) -> None:
        if max_primary_attempts < 1:
            raise ValueError("max_primary_attempts must be >= 1")
        self.provider = provider
        self.max_primary_attempts = max_primary_attempts

    def analyze_message(
        self,
        request: ReceptionistAnalysisRequest,
    ) -> ReceptionistAnalysisResult:
        started_at = perf_counter()
        prompt_version = get_current_receptionist_analysis_prompt_metadata().version
        llm_request = self._build_llm_request(
            request=request,
            prompt_version=prompt_version,
        )

        primary_attempt_count = 0
        used_repair = False
        last_failure_reason = LLMFailureReason.NONE
        last_error: str | None = None

        while primary_attempt_count < self.max_primary_attempts:
            primary_attempt_count += 1
            try:
                response = self.provider.complete(llm_request)
                parse_outcome = parse_structured_output_with_repair_flag(
                    raw_output=response.content,
                    model_type=ReceptionistLLMAnalysis,
                )
                used_repair = used_repair or parse_outcome.used_repair
                analysis = parse_outcome.value
                self._validate_safety(analysis)
                failure_reason = self._confidence_failure_reason(analysis)
                return self._success_result(
                    started_at=started_at,
                    response=response,
                    analysis=analysis,
                    primary_attempt_count=primary_attempt_count,
                    used_repair=used_repair,
                    failure_reason=failure_reason,
                    prompt_version=prompt_version,
                )
            except LLMProviderError as exc:
                last_failure_reason = provider_failure_reason(exc)
                last_error = str(exc)
            except StructuredOutputParseError as exc:
                last_failure_reason = parse_failure_reason_from_parse_error(
                    repair_attempted=exc.repair_attempted,
                )
                last_error = str(exc)
            except StructuredOutputValidationError as exc:
                last_failure_reason = LLMFailureReason.SCHEMA_VALIDATION_FAILED
                last_error = str(exc)
                break
            except LLMOutputSafetyViolation as exc:
                last_failure_reason = LLMFailureReason.SAFETY_VIOLATION
                last_error = str(exc)
                break
            except Exception as exc:
                logger.exception(
                    "llm_receptionist_analysis_unknown_error",
                    extra={
                        "event": "llm_receptionist_analysis_unknown_error",
                    },
                )
                last_failure_reason = LLMFailureReason.PROVIDER_EXCEPTION
                last_error = str(exc)
                break

            if not is_primary_provider_retryable(last_failure_reason):
                break

        return self._fallback_result(
            started_at=started_at,
            primary_attempt_count=primary_attempt_count,
            used_repair=used_repair,
            failure_reason=last_failure_reason,
            prompt_version=prompt_version,
            error=last_error or "LLM analysis failed",
        )

    def _build_llm_request(
        self,
        *,
        request: ReceptionistAnalysisRequest,
        prompt_version: str,
    ) -> LLMRequest:
        return LLMRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=build_receptionist_system_prompt(),
                ),
                LLMMessage(
                    role="user",
                    content=request.user_message,
                ),
            ],
            response_format="json",
            temperature=0.0,
            metadata={
                "component": "chat_receptionist",
                "prompt_version": prompt_version,
            },
        )

    def _validate_safety(self, analysis: ReceptionistLLMAnalysis) -> None:
        if analysis.intent == ReceptionistLLMIntent.EMERGENCY:
            return

        if analysis.urgency == ReceptionistUrgency.EMERGENCY:
            raise LLMOutputSafetyViolation(
                "Emergency urgency must use emergency intent",
            )

    def _confidence_failure_reason(
        self,
        analysis: ReceptionistLLMAnalysis,
    ) -> LLMFailureReason:
        if analysis.confidence < MIN_ACCEPTED_CONFIDENCE:
            return LLMFailureReason.LOW_CONFIDENCE

        return LLMFailureReason.NONE

    def _success_result(
        self,
        *,
        started_at: float,
        response: LLMResponse,
        analysis: ReceptionistLLMAnalysis,
        primary_attempt_count: int,
        used_repair: bool,
        failure_reason: LLMFailureReason,
        prompt_version: str,
    ) -> ReceptionistAnalysisResult:
        failure_category = failure_category_for_reason(failure_reason)
        return ReceptionistAnalysisResult(
            analysis=analysis,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_micros=response.estimated_cost_micros,
            latency_ms=self._elapsed_ms(started_at),
            attempt_count=primary_attempt_count,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=0,
            used_repair=used_repair,
            used_fallback=False,
            failure_reason=failure_reason,
            failure_category=failure_category,
            prompt_version=prompt_version,
            error=None,
        )

    def _fallback_result(
        self,
        *,
        started_at: float,
        primary_attempt_count: int,
        used_repair: bool,
        failure_reason: LLMFailureReason,
        prompt_version: str,
        error: str,
    ) -> ReceptionistAnalysisResult:
        logger.warning(
            "llm_receptionist_analysis_fallback",
            extra={
                "event": "llm_receptionist_analysis_fallback",
                "failure_reason": failure_reason.value,
                "failure_category": failure_category_for_reason(failure_reason).value,
                "primary_attempt_count": primary_attempt_count,
                "error": error,
            },
        )
        return ReceptionistAnalysisResult(
            analysis=fallback_receptionist_analysis(),
            model=None,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_micros=0,
            latency_ms=self._elapsed_ms(started_at),
            attempt_count=primary_attempt_count,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=0,
            used_repair=used_repair,
            used_fallback=True,
            failure_reason=failure_reason,
            failure_category=failure_category_for_reason(failure_reason),
            prompt_version=prompt_version,
            error=error,
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)
