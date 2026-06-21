from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

from app.ai.llm_provider import LLMMessage, LLMProvider, LLMProviderError, LLMRequest
from app.ai.receptionist_output import (
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
    fallback_receptionist_analysis,
)
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.ai.reliability import (
    MIN_ACCEPTED_CONFIDENCE,
    LLMFailureReason,
    LLMOutputSafetyViolation,
)
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output,
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
    used_fallback: bool
    failure_reason: LLMFailureReason
    error: str | None = None


class LLMReceptionistAnalysisService:
    def __init__(self, *, provider: LLMProvider) -> None:
        self.provider = provider

    def analyze_message(
        self,
        request: ReceptionistAnalysisRequest,
    ) -> ReceptionistAnalysisResult:
        started_at = perf_counter()
        attempt_count = 1

        llm_request = LLMRequest(
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
            },
        )

        try:
            response = self.provider.complete(llm_request)
            analysis = parse_structured_output(
                raw_output=response.content,
                model_type=ReceptionistLLMAnalysis,
            )
            self._validate_safety(analysis)
            failure_reason = self._confidence_failure_reason(analysis)
        except LLMProviderError as exc:
            return self._fallback_result(
                started_at=started_at,
                attempt_count=attempt_count,
                failure_reason=LLMFailureReason.PROVIDER_ERROR,
                error=str(exc),
            )
        except StructuredOutputParseError as exc:
            return self._fallback_result(
                started_at=started_at,
                attempt_count=attempt_count,
                failure_reason=LLMFailureReason.INVALID_JSON,
                error=str(exc),
            )
        except StructuredOutputValidationError as exc:
            return self._fallback_result(
                started_at=started_at,
                attempt_count=attempt_count,
                failure_reason=LLMFailureReason.SCHEMA_VALIDATION_ERROR,
                error=str(exc),
            )
        except LLMOutputSafetyViolation as exc:
            return self._fallback_result(
                started_at=started_at,
                attempt_count=attempt_count,
                failure_reason=LLMFailureReason.SAFETY_VIOLATION,
                error=str(exc),
            )
        except Exception as exc:
            logger.exception(
                "llm_receptionist_analysis_unknown_error",
                extra={
                    "event": "llm_receptionist_analysis_unknown_error",
                },
            )
            return self._fallback_result(
                started_at=started_at,
                attempt_count=attempt_count,
                failure_reason=LLMFailureReason.UNKNOWN_ERROR,
                error=str(exc),
            )

        return ReceptionistAnalysisResult(
            analysis=analysis,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_micros=response.estimated_cost_micros,
            latency_ms=self._elapsed_ms(started_at),
            attempt_count=attempt_count,
            used_fallback=False,
            failure_reason=failure_reason,
            error=None,
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

    def _fallback_result(
        self,
        *,
        started_at: float,
        attempt_count: int,
        failure_reason: LLMFailureReason,
        error: str,
    ) -> ReceptionistAnalysisResult:
        logger.warning(
            "llm_receptionist_analysis_fallback",
            extra={
                "event": "llm_receptionist_analysis_fallback",
                "failure_reason": failure_reason.value,
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
            attempt_count=attempt_count,
            used_fallback=True,
            failure_reason=failure_reason,
            error=error,
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)