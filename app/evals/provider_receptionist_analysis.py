from __future__ import annotations

from dataclasses import dataclass

from app.ai.receptionist_output import ReceptionistLLMAnalysis
from app.ai.reliability import LLMFailureReason
from app.evals.receptionist_analysis import (
    EvaluationCaseResult,
    EvaluationMode,
    EvaluationSummary,
    ReceptionistAnalysisEvalCase,
    build_evaluation_summary,
    evaluate_receptionist_analysis_case_against_actual,
)
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)


@dataclass(frozen=True, slots=True)
class ProviderEvaluationCaseOutput:
    case_id: str
    provider: str
    model: str | None
    prompt_version: str
    actual: dict[str, object]
    failure_reason: str | None
    latency_ms: int | None
    input_tokens: int
    output_tokens: int


class ProviderReceptionistAnalysisEvaluator:
    def __init__(self, *, llm_analysis_service: LLMReceptionistAnalysisService) -> None:
        self._llm_analysis_service = llm_analysis_service
        self._last_case_outputs: tuple[ProviderEvaluationCaseOutput, ...] = ()

    @property
    def last_case_outputs(self) -> tuple[ProviderEvaluationCaseOutput, ...]:
        return self._last_case_outputs

    def evaluate(self, cases: list[ReceptionistAnalysisEvalCase]) -> EvaluationSummary:
        provider_name = _provider_name(self._llm_analysis_service)
        case_outputs: list[ProviderEvaluationCaseOutput] = []
        case_results: list[EvaluationCaseResult] = []

        for case in cases:
            try:
                analysis_result = self._llm_analysis_service.analyze_message(
                    ReceptionistAnalysisRequest(
                        user_message=case.input.message,
                        conversation_context={},
                    ),
                )
                actual = analysis_to_evaluation_actual(analysis_result.analysis)
                failure_reason = _provider_failure_reason(analysis_result.failure_reason)
                case_output = ProviderEvaluationCaseOutput(
                    case_id=case.id,
                    provider=provider_name,
                    model=analysis_result.model,
                    prompt_version=analysis_result.prompt_version,
                    actual=actual,
                    failure_reason=failure_reason,
                    latency_ms=analysis_result.latency_ms,
                    input_tokens=analysis_result.input_tokens,
                    output_tokens=analysis_result.output_tokens,
                )
                case_result = evaluate_receptionist_analysis_case_against_actual(
                    case,
                    actual,
                    prompt_version=analysis_result.prompt_version,
                )
            except Exception as exc:
                case_output = ProviderEvaluationCaseOutput(
                    case_id=case.id,
                    provider=provider_name,
                    model=None,
                    prompt_version=case.prompt_version,
                    actual={},
                    failure_reason=str(exc),
                    latency_ms=None,
                    input_tokens=0,
                    output_tokens=0,
                )
                case_result = EvaluationCaseResult(
                    case_id=case.id,
                    prompt_version=case.prompt_version,
                    passed=False,
                    field_results=(),
                    failure_reason=f"evaluation_error:{exc}",
                )

            case_outputs.append(case_output)
            case_results.append(case_result)

        self._last_case_outputs = tuple(case_outputs)
        return build_evaluation_summary(case_results, mode=EvaluationMode.PROVIDER)


def run_provider_receptionist_analysis_evaluation(
    *,
    cases: list[ReceptionistAnalysisEvalCase],
    llm_analysis_service: LLMReceptionistAnalysisService,
) -> EvaluationSummary:
    return ProviderReceptionistAnalysisEvaluator(
        llm_analysis_service=llm_analysis_service,
    ).evaluate(cases)


def analysis_to_evaluation_actual(analysis: ReceptionistLLMAnalysis) -> dict[str, object]:
    extracted = analysis.extracted
    return {
        "intent": analysis.intent.value,
        "urgency": analysis.urgency.value,
        "requires_human": analysis.requires_human,
        "safety_flags": list(analysis.safety_flags),
        "extracted": {
            "specialty": extracted.specialty,
            "doctor": extracted.doctor_name,
            "date": extracted.date,
            "time": extracted.time,
        },
    }


def _provider_name(service: LLMReceptionistAnalysisService) -> str:
    return type(service.provider).__name__


def _provider_failure_reason(failure_reason: LLMFailureReason) -> str | None:
    if failure_reason == LLMFailureReason.NONE:
        return None
    return failure_reason.value
